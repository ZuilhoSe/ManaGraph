from langgraph.prebuilt import create_react_agent
from llm_factory import LLMFactory
from tools import search_cards, list_inventory_cards


class ArchitectAgent:
    def __init__(self):
        self.llm = LLMFactory.get_llm()
        self.tools = [search_cards, list_inventory_cards]

        self.system_prompt = """
        You are a Magic: The Gathering deck architect specializing in Commander.
        Your task is to find synergies based on user requests using the 'search_cards' tool.

        INVENTORY RULES:
        - If the user says "focus on cards I own", start with 'owned_only=True'.
        - If that search returns empty or few results, call list_inventory_cards to see the real collection, then search the catalog with 'owned_only=False' for extra options.
        - Clearly label owned cards vs buy-list cards.

        SEARCH STRATEGY (CRITICAL):
        - Magic cards rarely use the exact phrase "global damage". They use phrases like "deals damage to each creature", "deals damage to all creatures", "destroy all creatures", or "board wipe".
        - ALWAYS formulate your search queries using standard Magic: The Gathering rules text terminology.
        - If a search yields no results, try different synonyms (e.g., instead of "global damage", try "damage to each creature").

        Prioritize synergies with the card effect and deck color identity. If colors aren't specified, ask.
        """

        self.graph = create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=self.system_prompt
        )

    def run(self, query: str):
        return self.graph.invoke({"messages": [("user", query)]})
