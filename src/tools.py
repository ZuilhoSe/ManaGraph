import contextvars
import json
from langchain.tools import tool
from hybrid_search import RAGSearcher
from inventory import get_card, list_inventory, move_card, FREE_POOL
from rules_validator import CommanderValidator
from deck_state import DeckState

_searcher = None

# Fallback for search_cards' owned_only when the model's tool call omits it.
# Whoever starts a run should call set_deck_owned_only(deck.owned_only) once
# beforehand; defaults to False (matching DeckState.owned_only's own default)
# if nobody does, so this is a strict improvement over a hardcoded literal.
_deck_owned_only: contextvars.ContextVar[bool] = contextvars.ContextVar("deck_owned_only", default=False)


def set_deck_owned_only(value: bool) -> None:
    _deck_owned_only.set(bool(value))


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
    owned_only: bool | None = None,
    limit: int = 5,
    max_card_price: float | None = None,
    currency: str = "usd",
    cmc_min: float | None = None,
    cmc_max: float | None = None,
    role: str = "",
):
    """
    Search Magic: The Gathering cards in the vector index and filter with SQLite.
    Returns JSON.

    Args:
        query: Semantic description of the card effect.
        colors: Allowed color identity, e.g. ["R", "U"].
        owned_only: True to search only owned cards, False for the full catalog.
            Leave unset to use the deck's own owned_only setting.
        limit: Maximum number of cards to return.
        max_card_price: Optional per-card price cap in `currency`. Owned copies are still returned.
        currency: usd or eur.
        cmc_min: Optional inclusive mana-value floor.
        cmc_max: Optional inclusive mana-value ceiling.
        role: Optional role class: land, ramp, draw, interaction, threat, token_producer, token_payoff.
    """
    if owned_only is None:
        owned_only = _deck_owned_only.get()
    results = _get_searcher().search_cards(
        query=query,
        allowed_colors=colors,
        owned_only=owned_only,
        limit=limit,
        max_card_price=max_card_price,
        currency=currency,
        cmc_min=cmc_min,
        cmc_max=cmc_max,
        role=role or None,
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
def get_card_info(name: str) -> str:
    """
    Look up one card by its exact name in the Oracle catalog (not the inventory --
    this checks whether the card exists at all, regardless of ownership). Returns JSON.

    Use this to confirm a commander or card name before assuming it's invalid. A
    search_cards miss is NOT proof the name is wrong -- that's a semantic search over
    card text and can miss an exact, real card. This is an exact lookup.
    """
    from catalog import get_oracle_card

    info = get_oracle_card(name)
    if not info:
        return _json({"ok": False, "error": f"'{name}' was not found in the catalog."})
    return _json({"ok": True, "card": info})


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
def diagnose_deck_json(deck_json: str) -> str:
    """
    Symbolic deck diagnosis: curve, avg CMC, lands, pips vs sources, role gaps, named deficits.
    Returns JSON. Does not use an LLM. Trust this over any count you might invent.
    """
    from mana import diagnose_deck

    try:
        data = json.loads(deck_json)
        if not isinstance(data, dict):
            return _json({"ok": False, "error": "deck_json must be a JSON object."})
    except json.JSONDecodeError as exc:
        return _json({"ok": False, "error": f"Invalid JSON for deck_json: {exc}"})

    report = diagnose_deck(DeckState.from_dict(data))
    report["ok"] = True
    return _json(report)


@tool
def score_card_json(deck_json: str, card_name: str, query: str = "") -> str:
    """
    Score one candidate against the current deck (geometry, roles, curve/mana shape).
    Returns JSON from the solver. Not an LLM judgment.
    """
    from solver import DeckSolver

    try:
        data = json.loads(deck_json)
        if not isinstance(data, dict):
            return _json({"ok": False, "error": "deck_json must be a JSON object."})
    except json.JSONDecodeError as exc:
        return _json({"ok": False, "error": f"Invalid JSON for deck_json: {exc}"})

    deck = DeckState.from_dict(data)
    breakdown = DeckSolver().score_breakdown(deck, card_name, query=query)
    breakdown["ok"] = "error" not in breakdown
    return _json(breakdown)


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


@tool
def fill_deck_json(deck_json: str, query: str = "", retrieve: bool = False) -> str:
    """
    Greedy-fill remaining slots from candidate_pool (and optional vector retrieval).
    Returns JSON with the updated DeckState. The committed list never exceeds 99.
    """
    from solver import DeckSolver

    try:
        data = json.loads(deck_json)
        if not isinstance(data, dict):
            return _json({"ok": False, "error": "deck_json must be a JSON object."})
    except json.JSONDecodeError as exc:
        return _json({"ok": False, "error": f"Invalid JSON for deck_json: {exc}"})

    deck = DeckState.from_dict(data)
    report = DeckSolver().fill(deck, query=query, retrieve=retrieve)
    report["deck"] = deck.to_dict()
    return _json(report)


@tool
def cut_deck_json(deck_json: str, query: str = "") -> str:
    """
    Cut over-budget / over-99 cards and swap weak 99 slots for better candidate_pool cards.
    Returns JSON with the updated DeckState. The committed list never exceeds 99.
    """
    from solver import DeckSolver

    try:
        data = json.loads(deck_json)
        if not isinstance(data, dict):
            return _json({"ok": False, "error": "deck_json must be a JSON object."})
    except json.JSONDecodeError as exc:
        return _json({"ok": False, "error": f"Invalid JSON for deck_json: {exc}"})

    deck = DeckState.from_dict(data)
    report = DeckSolver().cut(deck, query=query)
    report["deck"] = deck.to_dict()
    return _json(report)
