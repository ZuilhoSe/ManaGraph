import traceback
from langchain.tools import tool
from hybrid_search import RAGSearcher

searcher = RAGSearcher()

@tool
def buscar_cartas_no_banco(query: str, cores: list, apenas_inventario: bool = True, limite: int = 5):
    """
    Busca cartas de Magic: The Gathering em uma base de dados vetorial e filtra com SQLite.
    
    Args:
        query (str): Descrição semântica do efeito da carta.
        cores (list): Identidade de cor permitida (ex: ["R", "U"]).
        apenas_inventario (bool): True para buscar apenas no inventário, False para o catálogo geral.
        limite (int): Número máximo de cartas a retornar.
    """
    resultados = searcher.buscar_cartas(
        query=query,
        cores_permitidas=cores,
        apenas_inventario=apenas_inventario,
        limite=limite
    )
        
    if not resultados:
        return "Nenhuma carta encontrada com esses critérios."
            
    formato = ""
    for r in resultados:
        formato += f"Nome: {r['nome']}\nTexto: {r['texto']}\nPossui: {r['quantidade']}\n\n"
    return formato