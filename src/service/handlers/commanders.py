"""Commander autocomplete for the web UI.

Approximates rule 903.3: a legendary creature is always commander-eligible;
a legendary planeswalker/vehicle/background needs oracle text saying it can
be a commander. This is a type_line/oracle_text heuristic for fast
autocomplete, not a full rules implementation -- rules_validator.py already
owns the real legality check and is left untouched.
"""

import json
import sqlite3

from catalog import DB_NAME

_QUERY = """
SELECT name, type_line, color_identity
FROM cards
WHERE name LIKE ? COLLATE NOCASE
  AND type_line LIKE '%Legendary%'
  AND (
    type_line LIKE '%Creature%'
    OR (
      (type_line LIKE '%Planeswalker%' OR type_line LIKE '%Vehicle%' OR type_line LIKE '%Background%')
      AND (oracle_text LIKE '%can be your commander%' OR oracle_text LIKE '%as your commander%')
    )
  )
ORDER BY name
LIMIT ?
"""


def search_commanders(query: str, limit: int = 20) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    conn = sqlite3.connect(DB_NAME)
    try:
        rows = conn.execute(_QUERY, (f"%{query}%", limit)).fetchall()
    finally:
        conn.close()

    results = []
    for name, type_line, identity_json in rows:
        try:
            identity = json.loads(identity_json or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            identity = []
        results.append({"name": name, "type_line": type_line or "", "identity": identity})
    return results
