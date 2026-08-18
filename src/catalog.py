import json
import os
import sqlite3
from datetime import datetime, timezone

from inventory import get_card as get_inventory_card

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")

os.makedirs(DATA_DIR, exist_ok=True)


def parse_price(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def oracle_text_from_scryfall(card: dict) -> str:
    """Oracle cards store DFC/split text on card_faces, not top-level oracle_text."""
    text = (card.get("oracle_text") or "").strip()
    if text:
        return text
    faces = card.get("card_faces") or []
    parts = [(face.get("oracle_text") or "").strip() for face in faces]
    return "\n\n".join(part for part in parts if part)


def keywords_from_scryfall(card: dict) -> str:
    return json.dumps(card.get("keywords") or [])


def mana_cost_from_scryfall(card: dict) -> str:
    cost = card.get("mana_cost")
    if cost:
        return cost
    faces = card.get("card_faces") or []
    if not faces:
        return ""
    return faces[0].get("mana_cost") or ""


def ensure_schema(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mana_cost TEXT,
            cmc REAL,
            oracle_text TEXT,
            color_identity TEXT,
            type_line TEXT,
            legalities TEXT,
            price_usd REAL,
            price_eur REAL
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    if "price_usd" not in columns:
        conn.execute("ALTER TABLE cards ADD COLUMN price_usd REAL")
    if "price_eur" not in columns:
        conn.execute("ALTER TABLE cards ADD COLUMN price_eur REAL")
    if "keywords" not in columns:
        conn.execute("ALTER TABLE cards ADD COLUMN keywords TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
        (key, value),
    )


def stamp_price_snapshot(conn: sqlite3.Connection, card_count: int):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_meta(conn, "price_snapshot_at", now)
    set_meta(conn, "scryfall_bulk_type", "oracle_cards")
    set_meta(conn, "card_count", str(card_count))
    conn.commit()


def get_meta(db_path: str = DB_NAME) -> dict:
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        rows = conn.execute("SELECT key, value FROM catalog_meta").fetchall()
        return {key: value for key, value in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def _row_to_card(row: sqlite3.Row) -> dict:
    keys = set(row.keys())
    legalities_raw = row["legalities"] if "legalities" in keys else "{}"
    identity_raw = row["color_identity"] if "color_identity" in keys else "[]"
    return {
        "id": row["id"] if "id" in keys else "",
        "name": row["name"],
        "mana_cost": row["mana_cost"] if "mana_cost" in keys else "",
        "cmc": row["cmc"] if "cmc" in keys else 0.0,
        "oracle_text": row["oracle_text"] or "" if "oracle_text" in keys else "",
        "color_identity": json.loads(identity_raw or "[]"),
        "type_line": row["type_line"] or "" if "type_line" in keys else "",
        "legalities": json.loads(legalities_raw or "{}"),
        "price_usd": row["price_usd"] if "price_usd" in keys else None,
        "price_eur": row["price_eur"] if "price_eur" in keys else None,
        "keywords": json.loads(row["keywords"] or "[]") if "keywords" in keys and row["keywords"] else [],
    }


def get_oracle_card(name: str, db_path: str = DB_NAME) -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM cards WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM cards WHERE name LIKE ? COLLATE NOCASE",
                (f"{name} //%",),
            ).fetchone()
        if row is None:
            return None
        return _row_to_card(row)
    finally:
        conn.close()


def card_unit_price(card: dict | None, currency: str = "usd") -> float | None:
    if not card:
        return None
    if currency == "eur":
        return parse_price(card.get("price_eur"))
    return parse_price(card.get("price_usd"))


def acquisition_cost(quantity: int, owned_qty: int, unit_price: float | None, owned_cost_zero: bool):
    """Return (cost_or_none, owned_used, buy_qty, price_unknown)."""
    owned_used = min(max(owned_qty, 0), quantity)
    buy_qty = quantity - owned_used
    if not owned_cost_zero:
        if unit_price is None:
            return None, 0, quantity, True
        return unit_price * quantity, 0, quantity, False
    if buy_qty == 0:
        return 0.0, owned_used, 0, False
    if unit_price is None:
        return None, owned_used, buy_qty, True
    return unit_price * buy_qty, owned_used, buy_qty, False


def enrich_deck(deck, db_path: str = DB_NAME) -> dict:
    """Attach catalog, inventory, curve, and acquisition cost to a DeckState."""
    from deck_state import DeckState

    if isinstance(deck, dict):
        deck = DeckState.from_dict(deck)

    details = []
    budget_used = 0.0
    price_unknown = []
    curve = {str(i): 0 for i in range(0, 7)}
    curve["7+"] = 0
    land_count = 0

    names = list(deck.cards.keys())
    if deck.commander:
        names = [deck.commander] + names

    seen_commander = False
    for name in names:
        is_commander = bool(deck.commander) and name.lower() == deck.commander.lower() and not seen_commander
        if is_commander:
            seen_commander = True
            qty = 1
        else:
            qty = deck.cards.get(name, 0)
            if qty <= 0:
                continue

        info = get_oracle_card(name, db_path)
        inv = get_inventory_card(name, db_path)
        owned_qty = inv["total_quantity"] if inv else 0
        unit_price = card_unit_price(info, deck.currency)
        cost, owned_used, buy_qty, unknown = acquisition_cost(
            qty, owned_qty, unit_price, deck.owned_cost_zero
        )
        if unknown:
            price_unknown.append(name)
        elif cost is not None:
            budget_used += cost

        cmc = float(info["cmc"]) if info and info.get("cmc") is not None else 0.0
        type_line = info["type_line"] if info else ""
        is_land = "Land" in type_line
        if is_land and not is_commander:
            land_count += qty
        elif not is_commander:
            bucket = "7+" if cmc >= 7 else str(int(cmc))
            curve[bucket] = curve.get(bucket, 0) + qty

        details.append(
            {
                "name": info["name"] if info else name,
                "quantity": qty,
                "is_commander": is_commander,
                "owned_qty": owned_qty,
                "owned_used": owned_used,
                "buy_qty": buy_qty,
                "unit_price": unit_price,
                "acquisition_cost": cost,
                "price_unknown": unknown,
                "cmc": cmc,
                "type_line": type_line,
                "color_identity": info["color_identity"] if info else [],
                "in_catalog": info is not None,
            }
        )

    return {
        "details": details,
        "budget_used": round(budget_used, 2),
        "price_unknown": price_unknown,
        "curve": curve,
        "land_count": land_count,
        "slot_count": deck.slot_count(),
        "catalog_meta": get_meta(db_path),
    }
