from langgraph.prebuilt import create_react_agent
from llm_factory import LLMFactory
from tools import (
    lookup_inventory,
    list_inventory_cards,
    validate_deck_json,
)


class InventoryAgent:
    def __init__(self):
        self.llm = LLMFactory.get_llm()
        self.tools = [
            lookup_inventory,
            list_inventory_cards,
            validate_deck_json,
        ]
        self.system_prompt = """
        You are the Inventory Manager of a Magic: The Gathering Commander system.
        Tools return JSON. The Manager already applied the validated plan to DeckState.
        You do not approve legality; you report ownership facts.

        Your job:
        1. Collect every card name in the Manager's applied operations (added / substituted in) and check them
           all in ONE lookup_inventory call (it takes a list) -- never call it once per card.
        2. If the user asked for owned cards, suggest owned alternatives the Architect missed.
        3. You have no write tools. Allocation is a separate Manager-authorized operation.
        4. Substitutions are valid work. Do not tell the Architect to build 99 cards from scratch.

        Final message MUST be JSON (no markdown):
        {
          "owned": [{"name": "...", "owned_qty": 1, "need": 1}],
          "missing": [{"name": "...", "need": 1}],
          "substitutions": [{"out": "...", "in": "...", "in_owned": true}],
          "alternatives": [{"name": "...", "instead_of": "..."}],
          "moves": [],
          "notes": "short factual report"
        }
        """
        self.graph = create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=self.system_prompt,
        )

    def run(self, query: str):
        return self.graph.invoke({"messages": [("user", query)]})
