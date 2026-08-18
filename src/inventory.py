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


def _connect():
    global _did_migrate
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    if not _did_migrate:
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


def get_card(card_name: str) -> dict | None:
    conn = _connect()
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
            return None
        allocations = json.loads(row["allocations"] or "{}")
        return {
            "card_name": row["card_name"],
            "total_quantity": row["total_quantity"],
            "allocations": allocations,
            "available": allocations.get(FREE_POOL, 0),
        }
    finally:
        conn.close()


def list_inventory(location: str | None = None) -> list[dict]:
    conn = _connect()
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
