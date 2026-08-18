import json
from langchain.tools import tool
from hybrid_search import RAGSearcher
from inventory import consultar_carta, listar_inventario, mover_carta, FREE_POOL
from rules_validator import CommanderValidator

searcher = RAGSearcher()


def _format_allocations(allocations: dict) -> str:
    if not allocations:
        return "(none)"
    return ", ".join(f"{loc}: {qty}" for loc, qty in sorted(allocations.items()))


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
        formato += f"Nome: {r['nome']}\nTexto: {r['texto']}\nPossui: {r['quantidade']}\n"
        if r.get("alocacao"):
            formato += f"Alocacao: {_format_allocations(r['alocacao'])}\n"
        formato += "\n"
    return formato


@tool
def consultar_inventario(card_name: str) -> str:
    """Look up one owned card: total copies and where they are allocated (free pool vs decks)."""
    carta = consultar_carta(card_name)
    if not carta:
        return f"'{card_name}' is not in the inventory."
    return (
        f"Nome: {carta['card_name']}\n"
        f"Total: {carta['total_quantity']}\n"
        f"Livres ({FREE_POOL}): {carta['livre']}\n"
        f"Alocacao: {_format_allocations(carta['allocations'])}"
    )


@tool
def listar_cartas_do_inventario(localizacao: str = "") -> str:
    """
    List cards in the physical collection.
    Leave localizacao empty to list everything.
    Use 'pool_livre' for the free pool or a deck key such as 'deck_krenko'.
    """
    local = localizacao.strip() or None
    cartas = listar_inventario(local)
    if not cartas:
        alvo = local or "inventory"
        return f"No cards found in '{alvo}'."

    linhas = []
    for carta in cartas:
        if local:
            linhas.append(
                f"- {carta['card_name']}: {carta['quantidade']} in {carta['localizacao']}"
            )
        else:
            linhas.append(
                f"- {carta['card_name']}: total {carta['total_quantity']} "
                f"[{_format_allocations(carta['allocations'])}]"
            )
    return "\n".join(linhas)


@tool
def mover_carta_inventario(card_name: str, origem: str, destino: str, quantidade: int = 1) -> str:
    """
    Move copies of an owned card from one location to another.
    Typical locations: 'pool_livre' (free pool) and deck keys like 'deck_krenko'.
    Only call this when the user explicitly asked to allocate, add, or remove cards from a deck.
    """
    resultado = mover_carta(card_name, origem, destino, quantidade)
    if not resultado.get("ok"):
        return f"MOVE FAILED: {resultado.get('erro')}"
    return (
        f"Moved {resultado['moved']}x {resultado['card_name']} "
        f"from '{resultado['from']}' to '{resultado['to']}'.\n"
        f"Alocacao agora: {_format_allocations(resultado['allocations'])}"
    )


@tool
def validar_regras_commander(comandante: str, cartas_json: str) -> str:
    """
    Deterministically validate Commander color identity and singleton rules.

    Args:
        comandante: Commander card name, e.g. "Krenko, Mob Boss".
        cartas_json: JSON object of card name -> quantity, e.g. '{"Sol Ring": 1, "Mountain": 35}'.
    """
    try:
        deck_list = json.loads(cartas_json)
        if not isinstance(deck_list, dict):
            return "cartas_json must be a JSON object of card name -> quantity."
    except json.JSONDecodeError as exc:
        return f"Invalid JSON for cartas_json: {exc}"

    validador = CommanderValidator()
    try:
        relatorio = validador.validate_deck(comandante, deck_list)
    finally:
        validador.fechar_conexao()

    if "erro" in relatorio:
        return relatorio["erro"]

    linhas = [
        f"Comandante: {relatorio['comandante']}",
        f"Identidade: {relatorio['identidade_comandante']}",
        f"Valido: {'SIM' if relatorio['valido'] else 'NAO'}",
    ]
    if relatorio.get("erros_de_cor"):
        linhas.append("Erros de cor: " + "; ".join(relatorio["erros_de_cor"]))
    if relatorio.get("erros_de_singleton"):
        linhas.append("Erros de singleton: " + "; ".join(relatorio["erros_de_singleton"]))
    if relatorio.get("cartas_desconhecidas"):
        linhas.append("Cartas desconhecidas: " + ", ".join(relatorio["cartas_desconhecidas"]))
    return "\n".join(linhas)
