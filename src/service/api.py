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
from service.handlers.inventory import list_inventory_cards

app = FastAPI(title="ManaGraph API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/inventory")
def get_inventory():
    return {"cards": list_inventory_cards()}


@app.get("/api/commanders")
def get_commanders(q: str = "", limit: int = 20):
    return {"commanders": search_commanders(q, limit)}


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
