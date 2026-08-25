"""Backend for the web UI's Analysis tab: exposes DeckSolver.score_breakdown()
(computed but never surfaced by the normal agent run) as a read-only,
recomputable-on-demand view over any commander + card list.

Isolated from the core agent modules the same way the rest of service/
handlers/ is -- this only calls into solver.py/deck_state.py/archetypes.py,
never edits them.
"""

from __future__ import annotations

from archetypes import infer_archetype
from catalog import get_oracle_card
from deck_state import DeckState
from solver import DeckSolver


def _resolve_archetype(commander: str, archetype: str | None) -> str:
    if archetype:
        return archetype
    info = get_oracle_card(commander) if commander else None
    if not info:
        return "generic"
    return infer_archetype(info.get("oracle_text") or "")


def _attach_searcher(solver: DeckSolver) -> None:
    """Best-effort: same lazy RAGSearcher.shared() pattern DeckSolver._retrieve()
    already uses. Without this, _geometry_cos() only has data/card_views.npz to
    fall back on -- a separate offline artifact (build_dataset.py) that a
    machine running only the web app's own indexing (deck_run.py's
    _ensure_search_index, which builds the live Chroma collection but not the
    npz) never generates, silently degrading every score to the Jaccard
    fallback. Swallows failures so an unreachable Chroma still returns
    Jaccard-only scores instead of a 500."""
    if solver.searcher is not None:
        return
    try:
        from hybrid_search import RAGSearcher

        solver.searcher = RAGSearcher.shared()
    except Exception:
        pass


def analyze_deck(
    commander: str,
    cards: dict[str, int],
    archetype: str | None = None,
    pool: dict[str, int] | None = None,
) -> dict:
    """Scores every card in `cards` against the commander (score_breakdown),
    plus -- if `pool` is given -- every pool card not already in `cards`, so
    the UI can show both "why is this card in the deck" and "why didn't this
    other one make it"."""
    resolved_archetype = _resolve_archetype(commander, archetype)
    deck = DeckState.from_dict(
        {"commander": commander, "cards": cards, "archetype": resolved_archetype}
    )
    if deck.commander and not deck.identity:
        cmd = get_oracle_card(deck.commander)
        if cmd:
            deck.identity = list(cmd["color_identity"])

    solver = DeckSolver()
    _attach_searcher(solver)
    solver.warm_embeddings([commander, *deck.card_list(), *(pool or {})])
    deck_cards = [solver.score_breakdown(deck, name) for name in deck.card_list()]
    deck_cards.sort(key=lambda c: c["total"], reverse=True)

    pool_cards = None
    if pool:
        deck_keys = {name.lower() for name in deck.card_list()}
        pool_cards = []
        for name in pool:
            if name.lower() in deck_keys:
                continue
            breakdown = solver.score_breakdown(deck, name)
            ok, reason = solver.can_add(deck, name)
            breakdown["eligible"] = ok
            breakdown["reason"] = None if ok else reason
            pool_cards.append(breakdown)
        pool_cards.sort(key=lambda c: c["total"], reverse=True)

    return {
        "archetype": resolved_archetype,
        "identity": deck.identity,
        "deck_cards": deck_cards,
        "pool_cards": pool_cards,
    }
