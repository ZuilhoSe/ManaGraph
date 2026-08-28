import json
import sqlite3
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")

FREE_POOL = "free_pool"
_LEGACY_FREE_POOL = "pool_livre"
_did_migrate = False


def _connect(db_path: str = DB_NAME):
    global _did_migrate
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if db_path == DB_NAME and not _did_migrate:
        _migrate_free_pool_key(conn)
        _did_migrate = True
    return conn


def _migrate_free_pool_key(conn):
    try:
        rows = conn.execute("SELECT card_name, allocations FROM inventory").fetchall()
    except sqlite3.OperationalError:
        return
    for row in rows:
        allocations = json.loads(row["allocations"] or "{}")
        if _LEGACY_FREE_POOL not in allocations:
            continue
        allocations[FREE_POOL] = allocations.get(FREE_POOL, 0) + allocations.pop(_LEGACY_FREE_POOL)
        conn.execute(
            "UPDATE inventory SET allocations = ? WHERE card_name = ?",
            (json.dumps(allocations), row["card_name"]),
        )
    conn.commit()


def _clean_allocations(allocations: dict) -> dict:
    cleaned = {}
    for location, qty in allocations.items():
        if qty > 0:
            cleaned[location] = qty
    return cleaned


def _split_face_query_name(card_name: str) -> str | None:
    """Split/room/DFC names are sometimes pasted as 'Front/Back' (single slash,
    no spaces) instead of Scryfall's 'Front // Back'. Normalize when unambiguous."""
    if "//" in card_name or "/" not in card_name:
        return None
    parts = [part.strip() for part in card_name.split("/")]
    if len(parts) != 2 or not all(parts):
        return None
    return f"{parts[0]} // {parts[1]}"


def get_card(card_name: str, db_path: str = DB_NAME) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT card_name, total_quantity, allocations
            FROM inventory
            WHERE card_name = ? COLLATE NOCASE
            """,
            (card_name,),
        ).fetchone()
        if not row:
            normalized = _split_face_query_name(card_name)
            if normalized:
                row = conn.execute(
                    """
                    SELECT card_name, total_quantity, allocations
                    FROM inventory
                    WHERE card_name = ? COLLATE NOCASE
                    """,
                    (normalized,),
                ).fetchone()
        if not row:
            return None
        allocations = json.loads(row["allocations"] or "{}")
        return {
            "card_name": row["card_name"],
            "total_quantity": row["total_quantity"],
            "allocations": allocations,
            "available": allocations.get(FREE_POOL, 0),
        }
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def get_cards(card_names: list[str], db_path: str = DB_NAME) -> dict[str, dict | None]:
    """Batch version of get_card: one query for many names instead of one round
    trip per name. Returns a dict keyed by each name as given in `card_names`
    (duplicates collapsed), mapping to its inventory record or None if not found."""
    names = list(dict.fromkeys(card_names))
    if not names:
        return {}
    results: dict[str, dict | None] = {name: None for name in names}
    lookup_names = list(names)
    for name in names:
        normalized = _split_face_query_name(name)
        if normalized:
            lookup_names.append(normalized)

    conn = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in lookup_names)
        rows = conn.execute(
            f"""
            SELECT card_name, total_quantity, allocations
            FROM inventory
            WHERE card_name COLLATE NOCASE IN ({placeholders})
            """,
            lookup_names,
        ).fetchall()
        by_lower = {row["card_name"].lower(): row for row in rows}

        for name in names:
            row = by_lower.get(name.lower())
            if not row:
                normalized = _split_face_query_name(name)
                if normalized:
                    row = by_lower.get(normalized.lower())
            if not row:
                continue
            allocations = json.loads(row["allocations"] or "{}")
            results[name] = {
                "card_name": row["card_name"],
                "total_quantity": row["total_quantity"],
                "allocations": allocations,
                "available": allocations.get(FREE_POOL, 0),
            }
        return results
    except sqlite3.OperationalError:
        return results
    finally:
        conn.close()


def list_inventory(location: str | None = None, db_path: str = DB_NAME) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT card_name, total_quantity, allocations FROM inventory ORDER BY card_name"
        ).fetchall()
        results = []
        for row in rows:
            allocations = json.loads(row["allocations"] or "{}")
            if location:
                qty = allocations.get(location, 0)
                if qty <= 0:
                    continue
                results.append(
                    {
                        "card_name": row["card_name"],
                        "quantity": qty,
                        "location": location,
                        "allocations": allocations,
                    }
                )
            else:
                results.append(
                    {
                        "card_name": row["card_name"],
                        "total_quantity": row["total_quantity"],
                        "allocations": allocations,
                        "available": allocations.get(FREE_POOL, 0),
                    }
                )
        return results
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def move_card(card_name: str, source: str, destination: str, quantity: int = 1) -> dict:
    if quantity <= 0:
        return {"ok": False, "error": "quantity must be a positive integer"}
    if source == destination:
        return {"ok": False, "error": "source and destination must be different locations"}

    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT card_name, allocations
            FROM inventory
            WHERE card_name = ? COLLATE NOCASE
            """,
            (card_name,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"'{card_name}' is not in the inventory"}

        canonical_name = row["card_name"]
        allocations = json.loads(row["allocations"] or "{}")
        available = allocations.get(source, 0)
        if available < quantity:
            return {
                "ok": False,
                "error": (
                    f"Only {available} cop{'y' if available == 1 else 'ies'} of "
                    f"'{canonical_name}' in '{source}'"
                ),
                "allocations": allocations,
            }

        allocations[source] = available - quantity
        allocations[destination] = allocations.get(destination, 0) + quantity
        allocations = _clean_allocations(allocations)
        total = sum(allocations.values())

        conn.execute(
            """
            UPDATE inventory
            SET total_quantity = ?, allocations = ?
            WHERE card_name = ?
            """,
            (total, json.dumps(allocations), canonical_name),
        )
        conn.commit()
        return {
            "ok": True,
            "card_name": canonical_name,
            "moved": quantity,
            "from": source,
            "to": destination,
            "allocations": allocations,
            "total_quantity": total,
        }
    finally:
        conn.close()


def execute_allocation(command) -> dict:
    """Execute a physical move only after a Manager-issued confirmation."""
    confirmation_id = getattr(command, "confirmation_id", "")
    if not confirmation_id:
        return {"ok": False, "error": "allocation requires explicit confirmation_id"}
    return move_card(
        command.card,
        command.source,
        command.destination,
        command.quantity,
    )
