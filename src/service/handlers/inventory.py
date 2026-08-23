"""Inventory listing for the web UI's Collection tab.

Only reads via catalog/inventory — never edits the core agent modules.
"""

import sqlite3

from catalog import DB_NAME, ensure_schema, get_oracle_card
from inventory import list_inventory

_CARD_FIELDS = "name, type_line, mana_cost, cmc, price_usd, price_eur"


def list_inventory_cards() -> list[dict]:
    entries = list_inventory()
    if not entries:
        return []

    # One batched lookup instead of one get_oracle_card() call (its own
    # connection + a COLLATE NOCASE full scan of ~38k cards, no index covers
    # it) per inventory row -- that was ~150ms/card, seconds for a ~150-card
    # collection just to render the tab. A single IN(...) scan does the same
    # work in one pass.
    names = [entry["card_name"] for entry in entries]
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        placeholders = ",".join("?" for _ in names)
        rows = conn.execute(
            f"SELECT {_CARD_FIELDS} FROM cards WHERE name COLLATE NOCASE IN ({placeholders})",
            names,
        ).fetchall()
    finally:
        conn.close()
    by_name = {row["name"].lower(): row for row in rows}

    items = []
    for entry in entries:
        card_name = entry["card_name"]
        row = by_name.get(card_name.lower())
        if row is not None:
            card = dict(row)
        else:
            # Rare fallback for split/DFC cards saved without the "Front //
            # Back" suffix (older inventory rows predating decks.py's name
            # canonicalization). One slow lookup per straggler, not per row.
            card = get_oracle_card(card_name) or {}
        items.append(
            {
                "name": card_name,
                "quantity": entry["total_quantity"],
                "allocations": entry["allocations"],
                "type_line": card.get("type_line", ""),
                "mana_cost": card.get("mana_cost", ""),
                "cmc": card.get("cmc"),
                "price_usd": card.get("price_usd"),
                "price_eur": card.get("price_eur"),
            }
        )
    return items


def delete_inventory_card(name: str) -> bool:
    """Removes a card from the collection entirely, regardless of which
    locations (free pool, saved decks, ...) currently hold copies of it."""
    conn = sqlite3.connect(DB_NAME)
    try:
        cursor = conn.execute("DELETE FROM inventory WHERE card_name = ? COLLATE NOCASE", (name,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
