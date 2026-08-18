from langgraph.prebuilt import create_react_agent
from llm_factory import LLMFactory
from tools import (
    consultar_inventario,
    listar_cartas_do_inventario,
    mover_carta_inventario,
    validar_regras_commander,
)


class InventoryAgent:
    def __init__(self):
        self.llm = LLMFactory.get_llm()
        self.tools = [
            consultar_inventario,
            listar_cartas_do_inventario,
            mover_carta_inventario,
            validar_regras_commander,
        ]
        self.system_prompt = """
        You are the Inventory Manager of a Magic: The Gathering Commander system.
        You work with the physical collection stored in SQLite.

        Your job:
        1. Read the user's request and the Architect's proposed cards.
        2. Check whether each proposed card is owned, how many copies, and where they sit
           (pool_livre = free pool, or a deck key such as deck_krenko).
        3. If the user asked to use owned cards, list relevant owned cards that the Architect missed.
           Call listar_cartas_do_inventario when the collection is small or when inventory search
           returned nothing useful.
        4. If a commander is named, call validar_regras_commander on the proposed cards.
        5. Call mover_carta_inventario ONLY if the user explicitly asked to add, allocate, or
           move cards into/out of a deck. Never move cards just because they were recommended.

        Write a short factual report covering:
        - owned vs not owned among the Architect's picks
        - useful owned alternatives, if any
        - validation result, if a commander was given
        - any moves you actually performed
        """
        self.graph = create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=self.system_prompt,
        )

    def run(self, query: str):
        return self.graph.invoke({"messages": [("user", query)]})
