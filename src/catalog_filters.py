"""Catalog queries by type-line fragment. Symbolic filters — not LLM card lists."""

from __future__ import annotations

import json
import sqlite3

from catalog import DB_NAME, _row_to_card, ensure_schema


def identity_ok(color_identity, allowed: list[str] | set[str] | None) -> bool:
    """True when card colors ⊆ commander identity (empty allowed = no filter)."""
    if allowed is None:
        return True
    allowed_set = set(allowed)
    if isinstance(color_identity, str):
        colors = set(json.loads(color_identity or "[]"))
    else:
        colors = set(color_identity or [])
    return colors.issubset(allowed_set)


def is_commander_legal(legalities) -> bool:
    if isinstance(legalities, str):
        legalities = json.loads(legalities or "{}")
    return (legalities or {}).get("commander") == "legal"


def type_line_has_fragment(type_line: str, fragment: str) -> bool:
    """Case-insensitive whole-word-ish match on type line tokens (Gate, Room, Snow)."""
    frag = (fragment or "").strip().lower()
    if not frag:
        return False
    tl = (type_line or "").lower()
    # Prefer token boundary so "Gate" does not match unrelated substrings awkwardly;
    # still allow "— Gate" / "Gate //" via simple containment with word edges.
    if frag in tl.split():
        return True
    # Subtype after em dash / hyphen often appears as "Land — Gate"
    for sep in ("—", "–", "-"):
        if sep in tl:
            subtypes = tl.split(sep, 1)[1]
            if frag in subtypes.replace("/", " ").split():
                return True
    # DFC faces: "Enchantment — Room // ..."
    for face in tl.split("//"):
        tokens = face.replace("—", " ").replace("–", " ").replace("-", " ").split()
        if frag in tokens:
            return True
    return frag in tl.split()


def cards_by_type_fragment(
    fragment: str,
    identity: list[str] | None = None,
    *,
    commander_legal: bool = True,
    lands_only: bool | None = None,
    db_path: str = DB_NAME,
) -> list[dict]:
    """Return oracle rows whose type_line contains `fragment`, under identity/legality.

    lands_only=True  → require 'Land' in type_line
    lands_only=False → exclude lands
    lands_only=None  → no land filter
    """
    frag = (fragment or "").strip()
    if not frag:
        return []
    if not __import__("os").path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        # SQL LIKE for recall; Python token check for precision.
        rows = conn.execute(
            "SELECT * FROM cards WHERE type_line LIKE ? COLLATE NOCASE ORDER BY name",
            (f"%{frag}%",),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        card = _row_to_card(row)
        tl = card.get("type_line") or ""
        if not type_line_has_fragment(tl, frag):
            continue
        if lands_only is True and "land" not in tl.lower():
            continue
        if lands_only is False and "land" in tl.lower():
            continue
        if not identity_ok(card.get("color_identity"), identity):
            continue
        if commander_legal and not is_commander_legal(card.get("legalities")):
            continue
        out.append(card)
    return out


def names_by_type_fragment(
    fragment: str,
    identity: list[str] | None = None,
    **kwargs,
) -> list[str]:
    return [c["name"] for c in cards_by_type_fragment(fragment, identity, **kwargs)]
