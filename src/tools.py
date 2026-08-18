import json
from langchain.tools import tool
from hybrid_search import RAGSearcher
from inventory import get_card, list_inventory, move_card, FREE_POOL
from rules_validator import CommanderValidator
from deck_state import DeckState

_searcher = None


def _get_searcher():
    global _searcher
    if _searcher is None:
        _searcher = RAGSearcher()
    return _searcher


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


@tool
def search_cards(
    query: str,
    colors: list,
    owned_only: bool = True,
    limit: int = 5,
    max_card_price: float | None = None,
    currency: str = "usd",
):
    """
    Search Magic: The Gathering cards in the vector index and filter with SQLite.
    Returns JSON.

    Args:
        query: Semantic description of the card effect.
        colors: Allowed color identity, e.g. ["R", "U"].
        owned_only: True to search only owned cards, False for the full catalog.
        limit: Maximum number of cards to return.
        max_card_price: Optional per-card price cap in `currency`. Owned copies are still returned.
        currency: usd or eur.
    """
    results = _get_searcher().search_cards(
        query=query,
        allowed_colors=colors,
        owned_only=owned_only,
        limit=limit,
        max_card_price=max_card_price,
        currency=currency,
    )
    if not results:
        return _json({"ok": True, "cards": [], "message": "No cards found with those criteria."})
    return _json({"ok": True, "cards": results})


@tool
def lookup_inventory(card_name: str) -> str:
    """Look up one owned card: total copies and where they are allocated (free pool vs decks). Returns JSON."""
    card = get_card(card_name)
    if not card:
        return _json({"ok": False, "error": f"'{card_name}' is not in the inventory."})
    return _json({"ok": True, "card": card, "free_pool": FREE_POOL})


@tool
def list_inventory_cards(location: str = "") -> str:
    """
    List cards in the physical collection. Returns JSON.
    Leave location empty to list everything.
    Use 'free_pool' for the unallocated pool or a deck key such as 'deck_krenko'.
    """
    loc = location.strip() or None
    cards = list_inventory(loc)
    if not cards:
        target = loc or "inventory"
        return _json({"ok": True, "cards": [], "message": f"No cards found in '{target}'."})
    return _json({"ok": True, "location": loc, "cards": cards})


@tool
def move_inventory_card(card_name: str, source: str, destination: str, quantity: int = 1) -> str:
    """
    Move copies of an owned card from one location to another. Returns JSON.
    Typical locations: 'free_pool' and deck keys like 'deck_krenko'.
    Only call this when the user explicitly asked to allocate, add, or remove cards from a deck.
    """
    result = move_card(card_name, source, destination, quantity)
    result["ok"] = bool(result.get("ok"))
    return _json(result)


@tool
def validate_commander_rules(commander: str, cards_json: str, constraints_json: str = "") -> str:
    """
    Deterministically validate a Commander list. Returns JSON.

    Args:
        commander: Commander card name, e.g. "Krenko, Mob Boss".
        cards_json: JSON object of card name -> quantity, e.g. '{"Sol Ring": 1, "Mountain": 35}'.
        constraints_json: Optional JSON with owned_only, require_complete, max_card_price, budget_cap, currency.
    """
    try:
        deck_list = json.loads(cards_json)
        if not isinstance(deck_list, dict):
            return _json({"ok": False, "error": "cards_json must be a JSON object of card name -> quantity."})
    except json.JSONDecodeError as exc:
        return _json({"ok": False, "error": f"Invalid JSON for cards_json: {exc}"})

    constraints = {}
    if constraints_json:
        try:
            parsed = json.loads(constraints_json)
            if isinstance(parsed, dict):
                constraints = parsed
        except json.JSONDecodeError as exc:
            return _json({"ok": False, "error": f"Invalid JSON for constraints_json: {exc}"})

    validator = CommanderValidator()
    report = validator.validate_deck(commander, deck_list, **constraints)
    report["ok"] = "error" not in report
    return _json(report)


@tool
def validate_deck_json(deck_json: str) -> str:
    """
    Validate a full DeckState JSON object (commander, cards, budget caps). Returns JSON.
    """
    try:
        data = json.loads(deck_json)
        if not isinstance(data, dict):
            return _json({"ok": False, "error": "deck_json must be a JSON object."})
    except json.JSONDecodeError as exc:
        return _json({"ok": False, "error": f"Invalid JSON for deck_json: {exc}"})

    validator = CommanderValidator()
    report = validator.validate_deck_state(DeckState.from_dict(data))
    report["ok"] = "error" not in report
    return _json(report)
