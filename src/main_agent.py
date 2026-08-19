from typing import TypedDict, Annotated, List, Any
import json
import os
import re
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage

from architect_agent import ArchitectAgent
from inventory_agent import InventoryAgent
from supervisor_agent import SupervisorAgent
from deck_state import DeckState, extract_json, infer_task, proposal_has_work
from catalog import enrich_deck, get_oracle_card
from mana import diagnose_deck
from rules_validator import CommanderValidator
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
    supervisor_decision: str
    validation: dict
    solver_report: dict


architect = ArchitectAgent()
inventory_manager = InventoryAgent()
supervisor = SupervisorAgent()


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
        diagnosis = diagnose_deck(deck)
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
    context = (
        f"User request: {state['user_query']}\n\n"
        f"Current deck JSON:\n{json.dumps(deck.summary(), indent=2)}\n\n"
        f"Symbolic diagnosis (do not recompute):\n{json.dumps(diag_view, indent=2, default=str)}"
        f"{_recent_feedback(state)}"
    )
    result = architect.run(context)
    architect_reply = to_text(result["messages"][-1].content)
    proposal = extract_json(architect_reply) or {}
    return {
        "messages": [AIMessage(content=architect_reply, name="architect")],
        "architect_reply": architect_reply,
        "proposal": proposal,
        "iterations": state["iterations"] + 1,
    }


def inventory_node(state: GraphState):
    print("\n[Node: Inventory] Applying delta and checking collection...")
    deck = DeckState.from_dict(state.get("deck"))
    proposal = state.get("proposal") or extract_json(state.get("architect_reply") or "") or {}
    parse_error = None
    if not proposal_has_work(proposal):
        parse_error = "Architect output was not valid JSON with a delta (add, remove, substitute, or buy_list)."
    else:
        commander = proposal.get("commander") or deck.commander
        if commander and not (proposal.get("identity") or deck.identity):
            info = get_oracle_card(commander)
            if info:
                proposal.setdefault("identity", info["color_identity"])
        deck.apply_delta(proposal)

    context = (
        f"User request: {state['user_query']}\n\n"
        f"Deck JSON:\n{json.dumps(deck.summary())}\n\n"
        f"Architect proposal:\n{json.dumps(proposal)}"
    )
    result = inventory_manager.run(context)
    agent_text = to_text(result["messages"][-1].content)
    report = {
        "parse_error": parse_error,
        "enrichment": enrich_deck(deck),
        "agent": extract_json(agent_text) or {"notes": agent_text},
    }
    payload = json.dumps(report, default=str)
    return {
        "messages": [AIMessage(content=payload, name="inventory")],
        "inventory_report": payload,
        "deck": deck.to_dict(),
        "proposal": proposal,
    }


def solver_node(state: GraphState):
    print("\n[Node: Solver] Repair / fill / cut...")
    deck = DeckState.from_dict(state.get("deck"))
    solver = DeckSolver()
    query = state.get("user_query") or ""
    report = solver.solve(
        deck, query=query, fill_to_99=bool(deck.require_complete and deck.commander)
    )
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
    proposal = state.get("proposal") or {}
    solver_report = state.get("solver_report") or {}
    solver_did = bool(
        ((solver_report.get("stripped") or {}).get("removed"))
        or ((solver_report.get("fill") or {}).get("added"))
        or ((solver_report.get("cut") or {}).get("swapped"))
        or ((solver_report.get("cut") or {}).get("removed"))
    )
    if not proposal_has_work(proposal) and not solver_did:
        validation = {
            "valid": False,
            "error": "Architect did not return an add, remove, substitute, candidate_pool, or buy_list.",
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
        "validation": evaluation.get("validation") or validation,
    }


def route_evaluation(state: GraphState):
    decision = (state.get("supervisor_decision") or "").upper()
    iterations = state.get("iterations", 0)

    if decision == "APPROVED":
        print("\n[Router] Supervisor APPROVED. Ending process.")
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
        deck = DeckState.from_dict(merged)
    else:
        deck.owned_only = deck.owned_only or flags["owned_only"]
        deck.require_complete = deck.require_complete or flags["require_complete"]
        if flags["intent"] != "build" or deck.slot_count() > 0:
            deck.intent = flags["intent"]
            if flags["intent"] != "build":
                deck.require_complete = False
    return {
        "messages": [],
        "user_query": query,
        "iterations": 0,
        "architect_reply": "",
        "inventory_report": "",
        "deck": deck.to_dict(),
        "proposal": {},
        "supervisor_decision": "",
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
    args = parser.parse_args()

    commander = args.commander.strip()
    query = " ".join(args.query).strip()
    if not query:
        if commander:
            query = f"Build me a full deck for {commander}."
        else:
            parser.error(
                'Pass a request, or --commander "Ertai Resurrected".\n'
                'Example: python src/main_agent.py --commander "Ertai Resurrected"'
            )
    if args.owned_only and "i own" not in query.lower() and "owned" not in query.lower():
        query += " Focus on cards I already own."

    print("Initializing ManaGraph Multi-Agent System...\n")
    print(f"User: {query}")

    seed = None
    if commander:
        info = get_oracle_card(commander)
        if not info:
            raise SystemExit(
                f"Commander '{commander}' was not found in the catalog. "
                "Run: python src/build_dataset.py"
            )
        print(f"Commander: {info['name']}  identity={info['color_identity']}")
        flags = infer_task(query)
        seed = DeckState(
            commander=info["name"],
            identity=list(info["color_identity"]),
            owned_only=args.owned_only or flags["owned_only"],
            require_complete=flags["require_complete"],
            intent=flags["intent"],
        )

    final_state = app.invoke(initial_graph_state(query, seed))

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

    deck = DeckState.from_dict(final_state.get("deck"))
    txt_path, json_path = write_deck_output(
        deck,
        extra={
            "query": query,
            "supervisor_decision": final_state.get("supervisor_decision"),
            "validation": final_state.get("validation"),
            "solver_report": final_state.get("solver_report"),
        },
    )
    print(f"\nWrote decklist to {txt_path}")
    print(f"Wrote run log to {json_path}")
