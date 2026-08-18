import sqlite3
import json
import re
import os
from inventory import FREE_POOL

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_DIR, exist_ok=True)

DB_NAME = os.path.join(DATA_DIR, "managraph.db")


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            card_name TEXT PRIMARY KEY,
            total_quantity INTEGER,
            allocations TEXT
        )
    """)
    conn.commit()
    return conn


def import_list(file_path, location_name):
    conn = init_db()
    cursor = conn.cursor()

    pattern = re.compile(r"^(\d+)x?\s+(.+)$")
    cards_to_add = {}

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                match = pattern.match(line)
                if match:
                    qty = int(match.group(1))
                    name = match.group(2).strip()
                    cards_to_add[name] = cards_to_add.get(name, 0) + qty
    except FileNotFoundError:
        print(f"Error: file '{file_path}' was not found.")
        return

    print(f"Processing {len(cards_to_add)} unique cards for location '{location_name}'...")

    for name, added_qty in cards_to_add.items():
        cursor.execute("SELECT allocations FROM inventory WHERE card_name = ?", (name,))
        row = cursor.fetchone()

        if row:
            allocations = json.loads(row[0])
            allocations[location_name] = allocations.get(location_name, 0) + added_qty
            total_quantity = sum(allocations.values())

            cursor.execute("""
                UPDATE inventory
                SET total_quantity = ?, allocations = ?
                WHERE card_name = ?
            """, (total_quantity, json.dumps(allocations), name))
        else:
            allocations = {location_name: added_qty}
            total_quantity = added_qty

            cursor.execute("""
                INSERT INTO inventory (card_name, total_quantity, allocations)
                VALUES (?, ?, ?)
            """, (name, total_quantity, json.dumps(allocations)))

    conn.commit()
    conn.close()
    print(f"Success! Inventory updated with cards from '{location_name}'.\n")


if __name__ == "__main__":
    pool_path = os.path.join(BASE_DIR, "data", "test_pool.txt")
    deck_path = os.path.join(BASE_DIR, "data", "test_deck.txt")

    import_list(pool_path, FREE_POOL)
    import_list(deck_path, "deck_krenko")
