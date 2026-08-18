"""Minimal read-only API for the web UI (web/). Run from src/:

    uvicorn service.api:app --reload --port 8000

Only exposes GET endpoints — no deck mutation, no graph invocation.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from catalog import get_oracle_card
from inventory import list_inventory

app = FastAPI(title="ManaGraph API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/inventory")
def get_inventory():
    items = []
    for entry in list_inventory():
        card = get_oracle_card(entry["card_name"]) or {}
        items.append(
            {
                "name": entry["card_name"],
                "quantity": entry["total_quantity"],
                "allocations": entry["allocations"],
                "type_line": card.get("type_line", ""),
                "mana_cost": card.get("mana_cost", ""),
                "cmc": card.get("cmc"),
                "price_usd": card.get("price_usd"),
                "price_eur": card.get("price_eur"),
            }
        )
    return {"cards": items}
