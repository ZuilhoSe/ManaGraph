from typing import TypedDict, Annotated, List, Any
import json
import os
import re
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage

from architect_agent import ManagerAgent
from supervisor_agent import SupervisorAgent
from deck_state import DeckState, diff_decks, extract_json, infer_task, proposal_has_work, _normalize_key
from catalog import enrich_deck, get_oracle_card
from contracts import ArchitectPlan, parse_architect_plan
from manager_core import apply_plan, build_intent_spec
from inventory import get_cards
from mana import diagnose_deck, strategy_from_name
from rules_validator import CommanderValidator, legal_commanders_in_pool, rank_commanders_by_pool_fit
from solver import DeckSolver

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")


def to_text(content: Any) -> str:
    """Gemini often returns content as a list of parts instead of a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            else:
                text = getattr(block, "text", None)
                parts.append(text if isinstance(text, str) else "")
        return "".join(parts)
    return str(content)


def write_deck_output(deck: DeckState, extra: dict | None = None) -> tuple[str, str]:
    """Write a Moxfield-style list and a JSON dump under data/."""
    os.makedirs(DATA_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", (deck.commander or "deck").lower()).strip("_") or "deck"
    txt_path = os.path.join(DATA_DIR, f"deck_{slug}.txt")
    json_path = os.path.join(DATA_DIR, f"deck_{slug}.json")
    lines = []
    if deck.commander:
        lines.append(f"1 {deck.commander}")
    for name, qty in sorted(deck.card_list().items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"{qty} {name}")
    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + ("\n" if lines else ""))
    payload = {"deck": deck.to_dict(), **(extra or {})}
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return txt_path, json_path


class GraphState(TypedDict):
    messages: Annotated[List, add_messages]
    user_query: str
    iterations: int
    architect_reply: str
    inventory_report: str
    deck: dict
    proposal: dict
    plan_result: dict
    intent_spec: dict
    supervisor_decision: str
    gate_decision: dict
    manager_explanation: str
    validation: dict
    solver_report: dict


manager = ManagerAgent()
# Compatibility alias for integrations that inspect the old graph module.
architect = manager
supervisor = SupervisorAgent()


def _pool_commander_note(deck: DeckState) -> str:
    """When the deck is pool-restricted, the Architect otherwise has no way to
    know which commander it's even allowed to pick -- deck.summary() carries
    the pool_only flag but not the (potentially ~200-card) card_pool itself,
    so a fresh build just falls back to picking a commander from general
    knowledge (e.g. Muldrotha for a graveyard Sultai deck) with no idea it's
    not physically available, which the Solver then strips and the Supervisor
    rejects -- burning a full architect/inventory/solver/supervisor round trip
    (and its LLM calls) on a choice that could never have worked. Skipped once
    a legal commander is already set, so later turns don't pay this query
    again for nothing.

    Plain listing leaves the pick itself to the Architect's training-data bias
    (e.g. defaulting to a famous BG commander even when the pool's color mix
    actually favors Sultai) -- deck.commander_by_pool_fit (advanced option,
    default False) opts into ranking that list by
    rules_validator.rank_commanders_by_pool_fit instead, so the Architect sees
    which identity the physical pool actually supports before choosing."""
    if not deck.pool_only or not deck.card_pool:
        return ""
    if deck.commander and _normalize_key(deck.commander) in {
        _normalize_key(n) for n in deck.card_pool
    }:
        return ""

    if deck.commander_by_pool_fit:
        ranked = rank_commanders_by_pool_fit(deck.card_pool)
        if not ranked:
            return (
                "\n\nPOOL COMMANDER NOTE: pool_only is set, but no commander-legal card "
                "exists in the allowed card_pool. Do not invent or recall a commander from "
                "general knowledge -- leave commander empty and say so in notes."
            )
        return (
            "\n\nLEGAL COMMANDERS IN THIS POOL, RANKED BY COLOR FIT (pool_only is set: "
            "you MUST pick the commander from this exact list, verbatim -- not from "
            "general knowledge. weighted_score measures how well the pool's actual color "
            "mix supports that identity -- higher is a stronger physical match. It says "
            "nothing about theme/archetype: among close or tied scores, use your own "
            "judgment on synergy, same as always):\n"
            + "\n".join(
                f"- {row['name']} ({''.join(row['identity']) or 'C'}): "
                f"weighted_score={row['weighted_score']}, on-color cards={row['raw_count']}"
                for row in ranked
            )
        )

    commanders = legal_commanders_in_pool(deck.card_pool)
    if not commanders:
        return (
            "\n\nPOOL COMMANDER NOTE: pool_only is set, but no commander-legal card "
            "exists in the allowed card_pool. Do not invent or recall a commander from "
            "general knowledge -- leave commander empty and say so in notes."
        )
    return (
        "\n\nLEGAL COMMANDERS IN THIS POOL (pool_only is set: you MUST pick the "
        "commander from this exact list, verbatim -- not from general knowledge, even "
        "a commander you're confident is strong for this request):\n"
        + "\n".join(f"- {name}" for name in commanders)
    )


def _recent_feedback(state: GraphState) -> str:
    if not state["messages"]:
        return ""
    chunks = []
    for msg in state["messages"][-4:]:
        name = getattr(msg, "name", "agent")
        chunks.append(f"[{name}]: {to_text(msg.content)}")
    return "\n\nPrevious agent messages:\n" + "\n".join(chunks)


def architect_node(state: GraphState):
    print("\n[Node: Architect] Thinking and searching...")
    deck = DeckState.from_dict(state.get("deck"))
    diagnosis = {}
    if deck.commander:
        diagnosis = diagnose_deck(deck, strategy=strategy_from_name(deck.mana_strategy))
    diag_view = {
        key: diagnosis.get(key)
        for key in (
            "land_count",
            "avg_cmc",
            "avg_cmc_band",
            "curve_profile",
            "fast_mana",
            "cheat_count",
            "curve",
            "roles",
            "pips",
            "sources",
            "pips_per_source",
            "deficits",
            "remaining_slots",
            "slot_count",
        )
        if diagnosis
    }
    if deck.archetype:
        diag_view["archetype"] = deck.archetype
    if deck.preferred_land_types or deck.theme_types or deck.land_types_strict:
        diag_view["preferred_land_types"] = list(deck.preferred_land_types)
        diag_view["theme_types"] = list(deck.theme_types)
        diag_view["land_types_strict"] = deck.land_types_strict
    context = (
        f"User request: {state['user_query']}\n\n"
        f"Validated intent spec:\n{json.dumps(build_intent_spec(state['user_query'], deck).model_dump(), indent=2)}\n\n"
        f"Current deck JSON:\n{json.dumps(deck.summary(), indent=2)}\n\n"
        f"Symbolic diagnosis (do not recompute):\n{json.dumps(diag_view, indent=2, default=str)}"
        f"{_pool_commander_note(deck)}"
        f"{_recent_feedback(state)}"
    )
    result = architect.run(context)
    architect_reply = to_text(result["messages"][-1].content)
    raw_proposal = extract_json(architect_reply) or {}
    try:
        proposal = parse_architect_plan(
            raw_proposal,
            base_revision=deck.revision,
        ).model_dump(by_alias=True)
    except Exception as exc:
        proposal = {"_parse_error": f"Invalid manager plan: {exc}"}
    return {
        "messages": [AIMessage(content=architect_reply, name="architect")],
        "architect_reply": architect_reply,
        "proposal": proposal,
        "iterations": state["iterations"] + 1,
    }


def inventory_node(state: GraphState):
    print("\n[Node: Manager] Applying validated plan and checking collection...")
    deck = DeckState.from_dict(state.get("deck"))
    proposal = state.get("proposal") or {}
    parse_error = proposal.get("_parse_error") or None
    plan_result = {}
    if not parse_error:
        try:
            plan = ArchitectPlan.model_validate(proposal)
            explicit_change = any(
                phrase in state["user_query"].lower()
                for phrase in (
                    "change commander",
                    "switch commander",
                    "set commander",
                    "use as commander",
                )
            )
            result = apply_plan(
                deck,
                plan,
                allow_commander_change=not deck.commander or explicit_change,
            )
            plan_result = result.model_dump(by_alias=True)
            if result.rejected:
                parse_error = "; ".join(item.message for item in result.rejected)
        except Exception as exc:
            parse_error = f"Manager plan rejected: {exc}"

    touched = []
    for operation in (plan_result.get("applied") or []):
        for key in ("card", "in", "out"):
            if operation.get(key):
                touched.append(operation[key])
    inventory_cards = get_cards(sorted(set(touched))) if touched else {}
    report = {
        "parse_error": parse_error,
        "enrichment": enrich_deck(deck),
        "inventory": inventory_cards,
        "plan_result": plan_result,
    }
    payload = json.dumps(report, default=str)
    return {
        "messages": [AIMessage(content=payload, name="inventory")],
        "inventory_report": payload,
        "deck": deck.to_dict(),
        "proposal": proposal,
        "plan_result": plan_result,
    }


def solver_node(state: GraphState):
    print("\n[Node: Solver] Repair / fill / cut...")
    deck = DeckState.from_dict(state.get("deck"))
    before = deck.to_dict()
    solver = DeckSolver()
    query = state.get("user_query") or ""
    report = solver.solve(
        deck, query=query, fill_to_99=bool(deck.require_complete and deck.commander)
    )
    if deck.card_list() != DeckState.from_dict(before).card_list():
        deck.revision += 1
    payload = json.dumps(report, default=str)
    stripped = len((report.get("stripped") or {}).get("removed") or [])
    added = len((report.get("fill") or {}).get("added") or [])
    swapped = len((report.get("cut") or {}).get("swapped") or [])
    print(f"[Solver] slots={deck.slot_count()} stripped={stripped} added={added} swapped={swapped}")
    return {
        "messages": [AIMessage(content=payload, name="solver")],
        "deck": deck.to_dict(),
        "solver_report": report,
    }


def supervisor_node(state: GraphState):
    print("\n[Node: Supervisor] Symbolic gate...")
    deck = DeckState.from_dict(state.get("deck"))
    solver_report = state.get("solver_report") or {}
    solver_did = bool(
        ((solver_report.get("stripped") or {}).get("removed"))
        or ((solver_report.get("fill") or {}).get("added"))
        or ((solver_report.get("cut") or {}).get("swapped"))
        or ((solver_report.get("cut") or {}).get("removed"))
    )
    if not state.get("plan_result", {}).get("state_changed") and not solver_did:
        validation = {
            "valid": False,
            "error": "Manager did not apply a state-changing plan.",
            "warnings": [],
        }
    else:
        validation = CommanderValidator().validate_deck_state(deck)

    evaluation = supervisor.evaluate(
        state["user_query"],
        state.get("architect_reply") or "",
        state.get("inventory_report", ""),
        validation=validation,
        deck=deck,
    )
    print(f"[Supervisor] {evaluation['decision']}")
    return {
        "messages": [AIMessage(content=evaluation["text"], name="supervisor")],
        "supervisor_decision": evaluation["decision"],
        "gate_decision": {
            key: evaluation.get(key)
            for key in ("schema_version", "decision", "valid", "reason_codes", "reasons", "warnings", "next_action")
        },
        "manager_explanation": evaluation.get("explanation", ""),
        "validation": evaluation.get("validation") or validation,
    }


def route_evaluation(state: GraphState):
    decision = (state.get("supervisor_decision") or "").upper()
    iterations = state.get("iterations", 0)
    next_action = (state.get("gate_decision") or {}).get("next_action", "")

    if decision == "APPROVED":
        print("\n[Router] Supervisor APPROVED. Ending process.")
        return END
    if next_action in ("clarify", "confirm"):
        print(f"\n[Router] Gate requires {next_action}. Ending process.")
        return END
    if iterations >= 3:
        print("\n[Router] Maximum iterations reached. Ending process to prevent infinite loop.")
        return END
    print("\n[Router] Supervisor REJECTED. Sending back to Architect.")
    return "architect"


workflow = StateGraph(GraphState)

workflow.add_node("architect", architect_node)
workflow.add_node("inventory", inventory_node)
workflow.add_node("solver", solver_node)
workflow.add_node("supervisor", supervisor_node)

workflow.set_entry_point("architect")
workflow.add_edge("architect", "inventory")
workflow.add_edge("inventory", "solver")
workflow.add_edge("solver", "supervisor")
workflow.add_conditional_edges(
    "supervisor",
    route_evaluation,
    {
        END: END,
        "architect": "architect",
    },
)

app = workflow.compile()


def initial_graph_state(query: str, deck: DeckState | dict | None = None) -> dict:
    has_cards = False
    if isinstance(deck, DeckState):
        has_cards = deck.slot_count() > 0
    elif isinstance(deck, dict):
        has_cards = bool(deck.get("cards"))
    flags = infer_task(query, has_cards)
    if deck is None:
        deck = DeckState(**flags)
    elif isinstance(deck, dict):
        merged = dict(deck)
        merged["intent"] = flags["intent"]
        if flags["owned_only"]:
            merged["owned_only"] = True
        merged["require_complete"] = flags["require_complete"]
        if flags.get("archetype") and flags["archetype"] != "generic":
            merged["archetype"] = flags["archetype"]
        if flags.get("preferred_land_types") and not merged.get("preferred_land_types"):
            merged["preferred_land_types"] = flags["preferred_land_types"]
        if flags.get("theme_types") and not merged.get("theme_types"):
            merged["theme_types"] = flags["theme_types"]
        if flags.get("land_types_strict"):
            merged["land_types_strict"] = True
        deck = DeckState.from_dict(merged)
    else:
        deck.owned_only = deck.owned_only or flags["owned_only"]
        deck.require_complete = deck.require_complete or flags["require_complete"]
        if flags.get("archetype") and (
            not deck.archetype or deck.archetype == "generic"
        ):
            deck.archetype = flags["archetype"]
        if flags.get("preferred_land_types") and not deck.preferred_land_types:
            deck.preferred_land_types = list(flags["preferred_land_types"])
        if flags.get("theme_types") and not deck.theme_types:
            deck.theme_types = list(flags["theme_types"])
        if flags.get("land_types_strict"):
            deck.land_types_strict = True
        if flags["intent"] != "build" or deck.slot_count() > 0:
            deck.intent = flags["intent"]
            if flags["intent"] != "build":
                deck.require_complete = False
    # Identity is a catalog-derived fact, not a user/LLM-controlled setting.
    if deck.commander:
        commander_info = get_oracle_card(deck.commander)
        if commander_info:
            deck.commander = commander_info["name"]
            deck.identity = list(commander_info.get("color_identity") or [])
    # Snapshot once, before any node touches the deck: this is what
    # price_cap_new_only compares against for the whole run, regardless of
    # how many architect/solver iterations follow.
    deck.baseline_cards = dict(deck.cards)
    return {
        "messages": [],
        "user_query": query,
        "iterations": 0,
        "architect_reply": "",
        "inventory_report": "",
        "deck": deck.to_dict(),
        "proposal": {},
        "plan_result": {},
        "intent_spec": build_intent_spec(query, deck).model_dump(),
        "supervisor_decision": "",
        "gate_decision": {},
        "manager_explanation": "",
        "validation": {},
        "solver_report": {},
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the ManaGraph multi-agent Commander builder."
    )
    parser.add_argument(
        "query",
        nargs="*",
        help='Natural-language request, e.g. "Build me a full deck for Ertai Resurrected."',
    )
    parser.add_argument(
        "--commander",
        default="",
        help='Seed the commander, e.g. "Ertai Resurrected".',
    )
    parser.add_argument(
        "--owned-only",
        action="store_true",
        help="Prefer cards from the local inventory.",
    )
    parser.add_argument(
        "--import",
        dest="import_path",
        default="",
        metavar="PATH",
        help="Load query + deck + configs from a JSON file (see --export) instead of "
        "typing/pasting them. {\"query\": ..., \"deck\": {...DeckState fields...}}.",
    )
    parser.add_argument(
        "--export",
        dest="export_path",
        default="",
        metavar="PATH",
        help="Write the resolved query + deck + configs to a JSON file for later --import, "
        "before invoking the agent graph.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the initial condition (and write --export, if given) without running "
        "the agent graph. Handy for generating a template to hand-edit.",
    )
    args = parser.parse_args()

    commander = args.commander.strip()
    query = " ".join(args.query).strip()

    seed = None
    if args.import_path:
        try:
            with open(args.import_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except OSError as exc:
            raise SystemExit(f"Could not read --import '{args.import_path}': {exc}")
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--import '{args.import_path}' is not valid JSON: {exc}")
        if not query:
            query = (loaded.get("query") or "").strip()
        seed = DeckState.from_dict(loaded.get("deck") or {})
        print(f"Loaded initial condition from {args.import_path}")

    if not query:
        if commander or (seed and seed.commander):
            query = f"Build me a full deck for {commander or seed.commander}."
        else:
            parser.error(
                'Pass a request, --commander "Ertai Resurrected", or --import a saved '
                "condition.\nExample: python src/main_agent.py --commander \"Ertai Resurrected\""
            )
    if args.owned_only and "i own" not in query.lower() and "owned" not in query.lower():
        query += " Focus on cards I already own."

    # --commander (explicit CLI flag) always wins; otherwise fall back to whatever
    # --import loaded. Only re-resolve against the catalog when there's an actual
    # commander to set or the imported deck is missing its color identity.
    if commander and (not seed or commander.lower() != (seed.commander or "").lower()):
        info = get_oracle_card(commander)
        if not info:
            raise SystemExit(
                f"Commander '{commander}' was not found in the catalog. "
                "Run: python src/build_dataset.py"
            )
        print(f"Commander: {info['name']}  identity={info['color_identity']}")
        flags = infer_task(query)
        if seed is None:
            seed = DeckState(
                commander=info["name"],
                identity=list(info["color_identity"]),
                owned_only=flags["owned_only"],
                require_complete=flags["require_complete"],
                intent=flags["intent"],
                archetype=flags["archetype"],
            )
        else:
            # set_commander() (not a raw attribute assignment) so that if the
            # imported deck's 99 happens to contain a card with this exact name,
            # it gets popped out of `cards` instead of being double-counted as
            # both the commander and a deck slot.
            seed.set_commander(info["name"])
            seed.identity = list(info["color_identity"])
            if flags.get("archetype") and (
                not seed.archetype or seed.archetype == "generic"
            ):
                seed.archetype = flags["archetype"]
    elif seed and seed.commander and not seed.identity:
        info = get_oracle_card(seed.commander)
        if not info:
            raise SystemExit(
                f"Commander '{seed.commander}' (from --import) was not found in the catalog. "
                "Run: python src/build_dataset.py"
            )
        seed.identity = list(info["color_identity"])

    # Single place that applies --owned-only, whether seed came from --import
    # or was just built above (its owned_only there reflects only the inferred
    # flags["owned_only"], not the CLI flag).
    if seed and args.owned_only:
        seed.owned_only = True

    if args.export_path:
        export_deck = seed if seed is not None else DeckState(**infer_task(query, False))
        export_dir = os.path.dirname(os.path.abspath(args.export_path))
        if export_dir:
            os.makedirs(export_dir, exist_ok=True)
        with open(args.export_path, "w", encoding="utf-8") as handle:
            json.dump({"query": query, "deck": export_deck.to_dict()}, handle, indent=2, default=str)
        print(f"Wrote initial condition to {args.export_path}")

    if args.dry_run:
        raise SystemExit(0)

    print("Initializing ManaGraph Multi-Agent System...\n")
    print(f"User: {query}")

    from hybrid_search import RAGSearcher

    print("Opening card index (once, not per search)...")
    RAGSearcher.shared()

    init_state = initial_graph_state(query, seed)
    final_state = app.invoke(init_state)

    print("\n=== FINAL DECK ===")
    print(json.dumps(final_state.get("deck"), indent=2, default=str))
    print("\n=== VALIDATION ===")
    print(json.dumps(final_state.get("validation"), indent=2, default=str))
    print(f"\nSupervisor: {final_state.get('supervisor_decision')}")
    if final_state.get("solver_report"):
        print("\n=== SOLVER ===")
        print(json.dumps(final_state.get("solver_report"), indent=2, default=str))
    if final_state.get("architect_reply"):
        print("\n=== ARCHITECT ===")
        print(final_state["architect_reply"])

    # Computed from the before/after card_list()s, not from any agent's
    # self-reported delta -- the architect/solver "swap" notes can drift
    # from what the deck state actually ended up with.
    deck_diff = diff_decks(init_state["deck"], final_state.get("deck"))
    print("\n=== SWAP (vs. starting deck) ===")
    print(json.dumps(deck_diff, indent=2, default=str))

    deck = DeckState.from_dict(final_state.get("deck"))
    txt_path, json_path = write_deck_output(
        deck,
        extra={
            "query": query,
            "supervisor_decision": final_state.get("supervisor_decision"),
            "validation": final_state.get("validation"),
            "solver_report": final_state.get("solver_report"),
            "deck_diff": deck_diff,
        },
    )
    print(f"\nWrote decklist to {txt_path}")
    print(f"Wrote run log to {json_path}")
