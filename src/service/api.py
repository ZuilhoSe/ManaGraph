"""API for the web UI (web/). Run from src/:

    uvicorn service.api:app --reload --port 8000

Routes only — request/response shaping lives in service/handlers/, kept apart
from the core agent modules (main_agent.py, architect_agent.py, solver.py, ...)
so backend AI work there can happen without needing to touch this service.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from service.handlers.commanders import search_commanders
from service.handlers.deck_run import cancel_deck_run, stream_deck_run
from service.handlers.decks import add_missing_cards, delete_deck, get_deck, list_decks, save_deck
from service.handlers.inventory import delete_inventory_card, list_inventory_cards

app = FastAPI(title="ManaGraph API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/inventory")
def get_inventory():
    return {"cards": list_inventory_cards()}


@app.delete("/api/inventory/{name:path}")
def delete_inventory_card_route(name: str):
    """Removes a card from the collection entirely (every location, not just
    the free pool) -- for clearing out junk/test entries from the Collection
    tab. Does not touch decks.py's own delete flow.

    Uses the `:path` converter (not the default `str`) because plain str
    params don't match a literal "/" even URL-encoded as %2F -- and card
    names routinely contain one, e.g. split cards like "Fire // Ice"."""
    if not delete_inventory_card(name):
        raise HTTPException(status_code=404, detail=f"'{name}' was not found in the collection.")
    return {"ok": True}


@app.get("/api/commanders")
def get_commanders(q: str = "", limit: int = 20):
    return {"commanders": search_commanders(q, limit)}


@app.get("/api/decks")
def get_decks():
    return {"decks": list_decks()}


class SaveDeckRequest(BaseModel):
    name: str
    commander: str
    cards: dict[str, int]


@app.post("/api/decks")
def post_deck(payload: SaveDeckRequest):
    """Saves (or re-saves) a named deck as inventory possession under its own
    location, and always returns the CommanderValidator report alongside it --
    the deck is saved regardless of what the report says, the UI surfaces
    problems as warnings rather than blocking on them."""
    try:
        return save_deck(payload.name, payload.commander, payload.cards)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class SyncDecksRequest(BaseModel):
    names: list[str]


@app.post("/api/decks/sync")
def sync_decks_route(payload: SyncDecksRequest):
    """Adds each deck's commander to the collection if it's missing (see
    add_missing_cards) -- covers decks saved before save_deck started
    including the commander automatically."""
    return add_missing_cards(payload.names)


@app.get("/api/decks/{name:path}")
def get_deck_detail(name: str):
    deck = get_deck(name)
    if not deck:
        raise HTTPException(status_code=404, detail=f"Deck '{name}' was not found.")
    return deck


@app.delete("/api/decks/{name:path}")
def delete_deck_route(name: str, remove_cards: bool = False):
    """Removes the deck record. By default its cards go back to the free pool
    (delete_deck's default); pass ?remove_cards=true to also drop them from
    the collection entirely instead of just unassigning them."""
    if not delete_deck(name, remove_cards=remove_cards):
        raise HTTPException(status_code=404, detail=f"Deck '{name}' was not found.")
    return {"ok": True}


class DeckRunRequest(BaseModel):
    query: str
    deck: dict | None = None


@app.post("/api/deck/run")
def run_deck(payload: DeckRunRequest):
    """Streams newline-delimited JSON: a "run_id" line first, then log lines as
    they're printed, one "node" event per finished agent node, then a closing
    "done"/"cancelled"/"error" event.
    """
    return StreamingResponse(stream_deck_run(payload.query, payload.deck), media_type="application/x-ndjson")


@app.post("/api/deck/run/{run_id}/cancel")
def cancel_run(run_id: str):
    """Best-effort: flags the run to stop before its next graph node, it can't
    abort an LLM call already in flight. The client aborts its own fetch the
    instant Stop is clicked regardless, which is what actually frees the UI."""
    if not cancel_deck_run(run_id):
        raise HTTPException(status_code=404, detail="Unknown or already-finished run_id.")
    return {"ok": True}
