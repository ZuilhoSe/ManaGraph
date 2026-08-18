from typing import TypedDict, Annotated, List, Any
import json
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage

from architect_agent import ArchitectAgent
from inventory_agent import InventoryAgent
from supervisor_agent import SupervisorAgent
from deck_state import DeckState, extract_json, infer_task, proposal_has_work
from catalog import enrich_deck, get_oracle_card
from rules_validator import CommanderValidator


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


def infer_constraints(query: str, has_cards: bool = False) -> dict:
    return infer_task(query, has_cards)


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
    context = (
        f"User request: {state['user_query']}\n\n"
        f"Current deck JSON:\n{json.dumps(deck.summary(), indent=2)}"
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


def supervisor_node(state: GraphState):
    print("\n[Node: Supervisor] Symbolic gate...")
    deck = DeckState.from_dict(state.get("deck"))
    proposal = state.get("proposal") or {}
    if not proposal_has_work(proposal):
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
workflow.add_node("supervisor", supervisor_node)

workflow.set_entry_point("architect")
workflow.add_edge("architect", "inventory")
workflow.add_edge("inventory", "supervisor")
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
    }


if __name__ == "__main__":
    print("Initializing ManaGraph Multi-Agent System...\n")

    query = "I need 3 blue and red cards to deal global damage, focus on cards I already own."
    print(f"User: {query}")

    final_state = app.invoke(initial_graph_state(query))

    print("\n=== FINAL DECK ===")
    print(json.dumps(final_state.get("deck"), indent=2, default=str))
    print("\n=== VALIDATION ===")
    print(json.dumps(final_state.get("validation"), indent=2, default=str))
    print(f"\nSupervisor: {final_state.get('supervisor_decision')}")
    if final_state.get("architect_reply"):
        print("\n=== ARCHITECT ===")
        print(final_state["architect_reply"])
