import json
import sqlite3
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")

FREE_POOL = "pool_livre"


def _connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def _clean_allocations(allocations: dict) -> dict:
    cleaned = {}
    for location, qty in allocations.items():
        if qty > 0:
            cleaned[location] = qty
    return cleaned


def consultar_carta(card_name: str) -> dict | None:
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
            "livre": allocations.get(FREE_POOL, 0),
        }
    finally:
        conn.close()


def listar_inventario(localizacao: str | None = None) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT card_name, total_quantity, allocations FROM inventory ORDER BY card_name"
        ).fetchall()
        resultados = []
        for row in rows:
            allocations = json.loads(row["allocations"] or "{}")
            if localizacao:
                qty = allocations.get(localizacao, 0)
                if qty <= 0:
                    continue
                resultados.append(
                    {
                        "card_name": row["card_name"],
                        "quantidade": qty,
                        "localizacao": localizacao,
                        "allocations": allocations,
                    }
                )
            else:
                resultados.append(
                    {
                        "card_name": row["card_name"],
                        "total_quantity": row["total_quantity"],
                        "allocations": allocations,
                        "livre": allocations.get(FREE_POOL, 0),
                    }
                )
        return resultados
    finally:
        conn.close()


def mover_carta(card_name: str, origem: str, destino: str, quantidade: int = 1) -> dict:
    if quantidade <= 0:
        return {"ok": False, "erro": "quantidade must be a positive integer"}
    if origem == destino:
        return {"ok": False, "erro": "origem and destino must be different locations"}

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
            return {"ok": False, "erro": f"'{card_name}' is not in the inventory"}

        canonical_name = row["card_name"]
        allocations = json.loads(row["allocations"] or "{}")
        disponivel = allocations.get(origem, 0)
        if disponivel < quantidade:
            return {
                "ok": False,
                "erro": (
                    f"Only {disponivel} cop{'y' if disponivel == 1 else 'ies'} of "
                    f"'{canonical_name}' in '{origem}'"
                ),
                "allocations": allocations,
            }

        allocations[origem] = disponivel - quantidade
        allocations[destino] = allocations.get(destino, 0) + quantidade
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
            "moved": quantidade,
            "from": origem,
            "to": destino,
            "allocations": allocations,
            "total_quantity": total,
        }
    finally:
        conn.close()
