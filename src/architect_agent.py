from langgraph.prebuilt import create_react_agent
from llm_factory import LLMFactory
from tools import buscar_cartas_no_banco

class ArchitectAgent:
    def __init__(self):
        self.llm = LLMFactory.get_llm()
        self.tools = [buscar_cartas_no_banco]
        
        self.system_prompt = """
        Você é um Arquiteto de Decks de Magic: The Gathering especialista em Commander.
        Sua tarefa é encontrar sinergias baseadas nas solicitações do usuário.
        Sempre que precisar buscar cartas, use a ferramenta 'buscar_cartas_no_banco'.
        
        REGRAS DE INVENTÁRIO:
        - Se o usuário disser "foco em cartas que eu já possuo", comece tentando com 'apenas_inventario=True'. 
        - Se a busca com True retornar erro ou poucas cartas, chame a ferramenta novamente definindo 'apenas_inventario=False' para encontrar as melhores opções do jogo geral e informe o usuário.
        
        Ao sugerir cartas, priorize sinergias com o efeito da carta e a identidade de cor do deck.
        Se o usuário não especificar cores, pergunte antes de buscar.
        """
        
        self.graph = create_react_agent(
            model=self.llm, 
            tools=self.tools, 
            prompt=self.system_prompt
        )

    def run(self, query: str):
        return self.graph.invoke({"messages": [("user", query)]})