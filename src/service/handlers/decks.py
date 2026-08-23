"""Saved decks for the web UI's Collection tab.

A "saved deck" is a name + commander (stored in a small `decks` table this
module owns) whose cards live as physical possession in the existing
`inventory` table, under a dedicated location `deck_<slug>` -- the same
allocations mechanism `inventory.py`/`import_inventory.py` already use, just
addressed by a stable per-deck location instead of a one-off string. Saving
re-validates via the existing `CommanderValidator` (rules_validator.py) but
never blocks: problems come back as part of the response for the UI to show
as warnings, matching how a deck is allowed to be a work in progress.

Only reads/writes the shared `inventory`/`cards` tables directly (same
pattern service/handlers/commanders.py already uses for `cards`) -- never
edits inventory.py/rules_validator.py themselves.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone

from catalog import DB_NAME, get_oracle_card
from deck_state import DeckState
from inventory import FREE_POOL, list_inventory
from rules_validator import CommanderValidator

LOCATION_PREFIX = "deck_"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "deck"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decks (
            name TEXT PRIMARY KEY,
            commander TEXT NOT NULL,
            location TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # inventory.py has no init of its own (only import_inventory.py's __main__
    # path creates this table) -- a fresh DB that never ran that script would
    # otherwise 500 the first save. Schema mirrors import_inventory.init_db().
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            card_name TEXT PRIMARY KEY,
            total_quantity INTEGER,
            allocations TEXT
        )
        """
    )


def _replace_location_cards(conn: sqlite3.Connection, location: str, cards: dict[str, int]) -> None:
    """Overwrites `location`'s cards to exactly `cards`, leaving every other
    location on every card untouched. Not a naive delete+insert: re-saving an
    edited deck must not double-count cards it shares with another location."""
    rows = conn.execute("SELECT card_name, total_quantity, allocations FROM inventory").fetchall()
    state = {
        row["card_name"]: {
            "total_quantity": row["total_quantity"],
            "allocations": json.loads(row["allocations"] or "{}"),
        }
        for row in rows
    }
    lower_index = {name.lower(): name for name in state}
    dirty: set[str] = set()

    for name, entry in state.items():
        if location in entry["allocations"]:
            entry["total_quantity"] -= entry["allocations"].pop(location)
            dirty.add(name)

    for raw_name, qty in cards.items():
        qty = int(qty)
        if qty <= 0:
            continue
        info = get_oracle_card(raw_name)
        canonical = info["name"] if info else raw_name.strip()
        if not canonical:
            continue
        existing_name = lower_index.get(canonical.lower())
        if existing_name is None:
            existing_name = canonical
            state[existing_name] = {"total_quantity": 0, "allocations": {}}
            lower_index[canonical.lower()] = existing_name
        entry = state[existing_name]
        entry["allocations"][location] = entry["allocations"].get(location, 0) + qty
        entry["total_quantity"] += qty
        dirty.add(existing_name)

    for name in dirty:
        entry = state[name]
        conn.execute(
            """
            INSERT INTO inventory (card_name, total_quantity, allocations)
            VALUES (?, ?, ?)
            ON CONFLICT(card_name) DO UPDATE SET
                total_quantity = excluded.total_quantity,
                allocations = excluded.allocations
            """,
            (name, entry["total_quantity"], json.dumps(entry["allocations"])),
        )


def _ensure_card_present(conn: sqlite3.Connection, location: str, card_name: str, min_qty: int = 1) -> bool:
    """Additive, unlike _replace_location_cards: bumps this one card's
    allocation at `location` up to at least min_qty without touching anything
    else there. Returns whether it actually changed anything."""
    info = get_oracle_card(card_name)
    canonical = info["name"] if info else card_name.strip()
    row = conn.execute(
        "SELECT card_name, allocations FROM inventory WHERE card_name = ? COLLATE NOCASE",
        (canonical,),
    ).fetchone()
    if row is not None:
        allocations = json.loads(row["allocations"] or "{}")
        if allocations.get(location, 0) >= min_qty:
            return False
        allocations[location] = min_qty
        conn.execute(
            "UPDATE inventory SET total_quantity = ?, allocations = ? WHERE card_name = ?",
            (sum(allocations.values()), json.dumps(allocations), row["card_name"]),
        )
    else:
        conn.execute(
            "INSERT INTO inventory (card_name, total_quantity, allocations) VALUES (?, ?, ?)",
            (canonical, min_qty, json.dumps({location: min_qty})),
        )
    return True


def _release_location_to_free_pool(conn: sqlite3.Connection, location: str) -> None:
    """Deleting a deck doesn't destroy its cards -- they're real physical
    copies, so they move back to the free pool instead of vanishing."""
    rows = conn.execute("SELECT card_name, allocations FROM inventory").fetchall()
    for row in rows:
        allocations = json.loads(row["allocations"] or "{}")
        qty = allocations.pop(location, 0)
        if qty <= 0:
            continue
        allocations[FREE_POOL] = allocations.get(FREE_POOL, 0) + qty
        conn.execute(
            "UPDATE inventory SET allocations = ? WHERE card_name = ?",
            (json.dumps(allocations), row["card_name"]),
        )


def _remove_location_cards(conn: sqlite3.Connection, location: str) -> None:
    """Unlike _release_location_to_free_pool, this actually drops the cards
    from the collection -- for when the user explicitly asks to delete a
    deck's cards, not just unassign them. Recomputes total_quantity from what
    allocations remain rather than subtracting, so it self-corrects instead of
    trusting the stored total to already match."""
    rows = conn.execute("SELECT card_name, allocations FROM inventory").fetchall()
    for row in rows:
        allocations = json.loads(row["allocations"] or "{}")
        if location not in allocations:
            continue
        allocations.pop(location)
        if not allocations:
            conn.execute("DELETE FROM inventory WHERE card_name = ?", (row["card_name"],))
        else:
            conn.execute(
                "UPDATE inventory SET total_quantity = ?, allocations = ? WHERE card_name = ?",
                (sum(allocations.values()), json.dumps(allocations), row["card_name"]),
            )


def get_deck(name: str) -> dict | None:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        conn.commit()
        row = conn.execute(
            "SELECT commander, location FROM decks WHERE name = ?", (name,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    cards = {c["card_name"]: c["quantity"] for c in list_inventory(location=row["location"])}
    # The commander is tracked separately (save_deck adds exactly 1 copy of it
    # to the same location, alongside the 99) -- drop it here so the edit
    # form's card list doesn't show it as a 100th entry duplicating the
    # Commander field.
    for card_name in list(cards):
        if card_name.lower() == row["commander"].lower():
            cards.pop(card_name)
    return {"name": name, "commander": row["commander"], "cards": cards}


def add_missing_cards(names: list[str]) -> dict:
    """Ensures each named deck's commander has at least 1 copy allocated at
    the deck's location. save_deck used to only write the 99-card body, never
    the commander itself -- decks saved before that fix are missing it from
    the collection. Can't recover any *other* card removed after the fact: a
    deck's card list has no record independent of inventory.allocations, so
    deleting that row from the Collection tab is the only way to lose track
    of "card X belongs to deck Y" today."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    added: list[dict] = []
    try:
        _ensure_schema(conn)
        for name in names:
            row = conn.execute(
                "SELECT commander, location FROM decks WHERE name = ?", (name,)
            ).fetchone()
            if not row:
                continue
            if _ensure_card_present(conn, row["location"], row["commander"]):
                added.append({"deck": name, "card": row["commander"]})
        conn.commit()
    finally:
        conn.close()
    return {"added": added}


def delete_deck(name: str, remove_cards: bool = False) -> bool:
    """remove_cards=False (default): cards go back to the free pool, still in
    the collection. remove_cards=True: cards are dropped from the collection
    entirely -- the user is choosing to give them up, not just unassign them."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        row = conn.execute("SELECT location FROM decks WHERE name = ?", (name,)).fetchone()
        if not row:
            return False
        if remove_cards:
            _remove_location_cards(conn, row["location"])
        else:
            _release_location_to_free_pool(conn, row["location"])
        conn.execute("DELETE FROM decks WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()
    return True


def list_decks() -> list[dict]:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        conn.commit()
        rows = conn.execute(
            "SELECT name, commander, location, created_at FROM decks ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()

    decks = []
    for row in rows:
        entries = list_inventory(location=row["location"])
        total = sum(c["quantity"] for c in entries)
        commander_qty = next(
            (c["quantity"] for c in entries if c["card_name"].lower() == row["commander"].lower()), 0
        )
        card_count = total - min(commander_qty, 1)
        decks.append(
            {
                "name": row["name"],
                "commander": row["commander"],
                "card_count": card_count,
                "created_at": row["created_at"],
            }
        )
    return decks


def save_deck(name: str, commander: str, cards: dict[str, int]) -> dict:
    name = (name or "").strip()
    commander = (commander or "").strip()
    if not name:
        raise ValueError("Deck name is required.")
    if not commander:
        raise ValueError("Commander is required.")

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        existing = conn.execute(
            "SELECT location, created_at FROM decks WHERE name = ?", (name,)
        ).fetchone()
        location = existing["location"] if existing else f"{LOCATION_PREFIX}{_slugify(name)}"
        created_at = (
            existing["created_at"]
            if existing
            else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )

        conn.execute(
            """
            INSERT INTO decks (name, commander, location, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET commander = excluded.commander
            """,
            (name, commander, location, created_at),
        )
        _replace_location_cards(conn, location, cards)
        _ensure_card_present(conn, location, commander)
        conn.commit()
    finally:
        conn.close()

    validator = CommanderValidator()
    deck_state = DeckState.from_dict({"commander": commander, "cards": cards})
    validation = validator.validate_deck_state(deck_state)

    return {
        "saved": True,
        "name": name,
        "commander": commander,
        "location": location,
        "validation": validation,
    }
