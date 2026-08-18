from typing import TypedDict, Annotated, List, Any
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage

from architect_agent import ArchitectAgent
from inventory_agent import InventoryAgent
from supervisor_agent import SupervisorAgent


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

class GraphState(TypedDict):
    messages: Annotated[List, add_messages]
    user_query: str
    iterations: int
    architect_reply: str
    inventory_report: str

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
    context = state["user_query"] + _recent_feedback(state)
    result = architect.run(context)
    architect_reply = to_text(result["messages"][-1].content)
    return {
        "messages": [AIMessage(content=architect_reply, name="architect")],
        "architect_reply": architect_reply,
        "iterations": state["iterations"] + 1
    }


def inventory_node(state: GraphState):
    print("\n[Node: Inventory] Checking collection and rules...")
    architect_reply = state.get("architect_reply") or to_text(state["messages"][-1].content)
    context = (
        f"User request: {state['user_query']}\n\n"
        f"Architect proposal:\n{architect_reply}"
    )
    result = inventory_manager.run(context)
    inventory_report = to_text(result["messages"][-1].content)
    return {
        "messages": [AIMessage(content=inventory_report, name="inventory")],
        "inventory_report": inventory_report,
    }


def supervisor_node(state: GraphState):
    print("\n[Node: Supervisor] Evaluating the Architect's work...")
    evaluation = to_text(supervisor.evaluate(
        state["user_query"],
        state.get("architect_reply") or to_text(state["messages"][-1].content),
        state.get("inventory_report", ""),
    ))
    return {
        "messages": [AIMessage(content=evaluation, name="supervisor")]
    }

def route_evaluation(state: GraphState):
    last_message = to_text(state["messages"][-1].content)
    iterations = state.get("iterations", 0)
    
    # Stop condition: The supervisor approved or we tried too many times
    if "APPROVED" in last_message.upper():
        print("\n[Router] Supervisor APPROVED. Ending process.")
        return END
    elif iterations >= 3:
        print("\n[Router] Maximum iterations reached. Ending process to prevent infinite loop.")
        return END
    else:
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
        "architect": "architect"
    }
)

app = workflow.compile()

if __name__ == "__main__":
    print("Initializing ManaGraph Multi-Agent System...\n")
    
    query = "I need 3 blue and red cards to deal global damage, focus on cards I already own."
    print(f"User: {query}")
    
    initial_state = {
        "messages": [],
        "user_query": query,
        "iterations": 0,
        "architect_reply": "",
        "inventory_report": "",
    }
    
    final_state = app.invoke(initial_state)
    
    print("\n=== FINAL RESULT ===")
    for msg in reversed(final_state["messages"]):
        if msg.name == "architect":
            print(to_text(msg.content))
            break
    if final_state.get("inventory_report"):
        print("\n=== INVENTORY REPORT ===")
        print(final_state["inventory_report"])