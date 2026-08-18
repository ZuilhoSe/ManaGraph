import json
from langchain.tools import tool
from hybrid_search import RAGSearcher
from inventory import get_card, list_inventory, move_card, FREE_POOL
from rules_validator import CommanderValidator

searcher = RAGSearcher()


def _format_allocations(allocations: dict) -> str:
    if not allocations:
        return "(none)"
    return ", ".join(f"{loc}: {qty}" for loc, qty in sorted(allocations.items()))


@tool
def search_cards(query: str, colors: list, owned_only: bool = True, limit: int = 5):
    """
    Search Magic: The Gathering cards in the vector index and filter with SQLite.

    Args:
        query: Semantic description of the card effect.
        colors: Allowed color identity, e.g. ["R", "U"].
        owned_only: True to search only owned cards, False for the full catalog.
        limit: Maximum number of cards to return.
    """
    results = searcher.search_cards(
        query=query,
        allowed_colors=colors,
        owned_only=owned_only,
        limit=limit
    )

    if not results:
        return "No cards found with those criteria."

    formatted = ""
    for result in results:
        formatted += f"Name: {result['name']}\nText: {result['text']}\nOwned: {result['quantity']}\n"
        if result.get("allocation"):
            formatted += f"Allocation: {_format_allocations(result['allocation'])}\n"
        formatted += "\n"
    return formatted


@tool
def lookup_inventory(card_name: str) -> str:
    """Look up one owned card: total copies and where they are allocated (free pool vs decks)."""
    card = get_card(card_name)
    if not card:
        return f"'{card_name}' is not in the inventory."
    return (
        f"Name: {card['card_name']}\n"
        f"Total: {card['total_quantity']}\n"
        f"Available ({FREE_POOL}): {card['available']}\n"
        f"Allocation: {_format_allocations(card['allocations'])}"
    )


@tool
def list_inventory_cards(location: str = "") -> str:
    """
    List cards in the physical collection.
    Leave location empty to list everything.
    Use 'free_pool' for the unallocated pool or a deck key such as 'deck_krenko'.
    """
    loc = location.strip() or None
    cards = list_inventory(loc)
    if not cards:
        target = loc or "inventory"
        return f"No cards found in '{target}'."

    lines = []
    for card in cards:
        if loc:
            lines.append(
                f"- {card['card_name']}: {card['quantity']} in {card['location']}"
            )
        else:
            lines.append(
                f"- {card['card_name']}: total {card['total_quantity']} "
                f"[{_format_allocations(card['allocations'])}]"
            )
    return "\n".join(lines)


@tool
def move_inventory_card(card_name: str, source: str, destination: str, quantity: int = 1) -> str:
    """
    Move copies of an owned card from one location to another.
    Typical locations: 'free_pool' and deck keys like 'deck_krenko'.
    Only call this when the user explicitly asked to allocate, add, or remove cards from a deck.
    """
    result = move_card(card_name, source, destination, quantity)
    if not result.get("ok"):
        return f"MOVE FAILED: {result.get('error')}"
    return (
        f"Moved {result['moved']}x {result['card_name']} "
        f"from '{result['from']}' to '{result['to']}'.\n"
        f"Allocation now: {_format_allocations(result['allocations'])}"
    )


@tool
def validate_commander_rules(commander: str, cards_json: str) -> str:
    """
    Deterministically validate Commander color identity and singleton rules.

    Args:
        commander: Commander card name, e.g. "Krenko, Mob Boss".
        cards_json: JSON object of card name -> quantity, e.g. '{"Sol Ring": 1, "Mountain": 35}'.
    """
    try:
        deck_list = json.loads(cards_json)
        if not isinstance(deck_list, dict):
            return "cards_json must be a JSON object of card name -> quantity."
    except json.JSONDecodeError as exc:
        return f"Invalid JSON for cards_json: {exc}"

    validator = CommanderValidator()
    try:
        report = validator.validate_deck(commander, deck_list)
    finally:
        validator.close()

    if "error" in report:
        return report["error"]

    lines = [
        f"Commander: {report['commander']}",
        f"Identity: {report['commander_identity']}",
        f"Valid: {'YES' if report['valid'] else 'NO'}",
    ]
    if report.get("color_errors"):
        lines.append("Color errors: " + "; ".join(report["color_errors"]))
    if report.get("singleton_errors"):
        lines.append("Singleton errors: " + "; ".join(report["singleton_errors"]))
    if report.get("unknown_cards"):
        lines.append("Unknown cards: " + ", ".join(report["unknown_cards"]))
    return "\n".join(lines)
