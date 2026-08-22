"""Shared card text for Chroma documents and hybrid (lexical + embedding) search.

Chroma documents are type + oracle only — never the card name. Name lives in
metadata for display and exact lookup. Embedding the name made queries like
\"draw a card\" rank \"Card Draw\" above Phyrexian Arena.
"""

from __future__ import annotations

import json
import re
import sqlite3

# Bump when the document string format changes; vectorize_cards forces rebuild.
DOCUMENT_FORMAT = "type_oracle_v1"

_STOP = {
    "a", "an", "the", "and", "or", "to", "of", "for", "on", "in", "with", "your",
    "you", "this", "that", "it", "is", "are", "be", "i", "me", "my", "want",
    "need", "give", "gives", "get", "gets", "card", "cards", "that", "which",
}

# Oracle templates keyed by family. If the user/role query hits a family, expand
# lexical search to every phrase in that family (Necro does not say "draw").
_FAMILY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "draw": (
        "draw",
        "card advantage",
        "draw engine",
        "draw engines",
        "cantrip",
        "card draw",
    ),
    "ramp": (
        "ramp",
        "mana rock",
        "add mana",
        "acceleration",
    ),
    "interaction": (
        "counter",
        "removal",
        "destroy target",
        "exile target",
        "bounce",
        "interaction",
    ),
    "mill": (
        "mill",
        "deck out",
        "self-mill",
        "self mill",
    ),
}

_FAMILY_PHRASES: dict[str, tuple[str, ...]] = {
    "draw": (
        "draw a card",
        "draw two cards",
        "draw three cards",
        "draw four cards",
        "draws a card",
        "draw x cards",
        "draw that many cards",
        "exile the top card of your library",
        "look at the top card of your library",
        "put it into your hand",
        "put that card into your hand",
        "you may draw",
    ),
    "ramp": (
        "add {",
        "add one mana",
        "add two mana",
        "search your library for a land",
        "search your library for a basic",
        "create a treasure",
        "treasure token",
    ),
    "interaction": (
        "counter target spell",
        "counter target",
        "destroy target creature",
        "destroy target permanent",
        "exile target creature",
        "exile target permanent",
        "return target",
        "fight target",
    ),
    "mill": (
        "mill ",
        "mills ",
        "library into their graveyard",
        "library into his or her graveyard",
    ),
}

# When the user asks for draw, these oracle templates count as "primary" matches
# (same scoring band as the literal query), not as weaker family expansions.
_DRAW_CORE_PHRASES = frozenset(
    {
        "draw a card",
        "draws a card",
        "draw two cards",
        "draw three cards",
        "draw four cards",
        "draw x cards",
        "draw that many cards",
        "you may draw",
        "you draw a card",
    }
)

_JUNK_TYPE_EXACT = {
    "card",
    "token",
    "emblem",
    "art series",
    "hero",
    "dungeon",
    "plane",
    "phenomenon",
    "scheme",
    "conspiracy",
    "vanguard",
    "stickers",
}


def card_document(type_line: str = "", oracle_text: str = "") -> str:
    """Text that gets embedded. Name must not appear here."""
    tl = (type_line or "").strip() or "Unknown type"
    ot = (oracle_text or "").strip() or "Vanilla creature / No abilities."
    return f"{tl}. Effect: {ot}"


def is_searchable_card(
    name: str = "",
    type_line: str = "",
    legalities=None,
    *,
    commander_only: bool = True,
) -> bool:
    """Drop tokens, theme cards, minigames, and non-Commander printings from search."""
    name_l = (name or "").lower()
    tl = (type_line or "").strip().lower()
    if not name_l:
        return False
    if tl in _JUNK_TYPE_EXACT:
        return False
    if "minigame" in name_l or "minigame" in tl:
        return False
    if "(cont'd)" in name_l or "cont'd" in name_l:
        return False
    if name_l.startswith("a-") and " // " not in name_l:
        # Alchemy rebalances often pollute semantic neighborhoods.
        return False
    if commander_only:
        status = _commander_status(legalities)
        if status not in (None, "legal"):
            # None = synthetic/test row without legalities; keep those.
            return False
    return True


def _commander_status(legalities) -> str | None:
    if legalities is None or legalities == "":
        return None
    if isinstance(legalities, str):
        try:
            legalities = json.loads(legalities)
        except json.JSONDecodeError:
            return None
    if not isinstance(legalities, dict):
        return None
    return legalities.get("commander")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def lexical_phrases(query: str) -> list[str]:
    """Phrases to run as case-insensitive substring matches over oracle_text."""
    q = " ".join((query or "").lower().split())
    if not q:
        return []

    phrases: list[str] = []
    seen: set[str] = set()

    def add(phrase: str):
        p = " ".join(phrase.lower().split())
        if len(p) < 4 or p in seen:
            return
        seen.add(p)
        phrases.append(p)

    # Short / oracle-shaped queries: use the whole string.
    if len(q) <= 120 and not q.endswith("?"):
        add(q)

    for family, triggers in _FAMILY_TRIGGERS.items():
        if any(t in q for t in triggers):
            for phrase in _FAMILY_PHRASES.get(family, ()):
                add(phrase)

    # Sliding windows of content words (3–6) for longer NL requests.
    words = [w for w in _tokens(q) if w not in _STOP]
    for n in (6, 5, 4, 3):
        if len(words) < n:
            continue
        for i in range(0, len(words) - n + 1):
            add(" ".join(words[i : i + n]))

    # Prefer longer phrases first (more specific LIKE hits).
    phrases.sort(key=len, reverse=True)
    return phrases[:24]


def _is_primary_match(query: str, oracle_text: str, matched_phrase: str | None) -> bool:
    ot = (oracle_text or "").lower()
    q = " ".join((query or "").lower().split())
    if q and q in ot:
        return True
    phrase = (matched_phrase or "").lower()
    if not phrase:
        return False
    # Draw-family queries: any core "draw N cards" template is primary.
    if phrase in _DRAW_CORE_PHRASES and any(
        t in q for t in _FAMILY_TRIGGERS["draw"]
    ):
        return True
    return False


def lexical_distance(query: str, oracle_text: str, matched_phrase: str | None = None) -> float:
    """Chroma-compatible distance (lower = better) for a lexical hit.

    Primary matches (the query string, or core draw templates on a draw query)
    outrank looser family expansions like impulse-draw / look-at-top.
    """
    ot = (oracle_text or "").lower()
    q = " ".join((query or "").lower().split())
    if not ot:
        return 0.95

    phrase = (matched_phrase or "").lower() if matched_phrase else ""
    primary = _is_primary_match(query, oracle_text, matched_phrase)
    if primary and q and q in ot:
        phrase = q
    elif primary and not phrase:
        for core in _DRAW_CORE_PHRASES:
            if core in ot:
                phrase = core
                break
    elif not phrase:
        return 0.65

    if not phrase or phrase not in ot:
        q_toks = {t for t in _tokens(q) if t not in _STOP}
        if not q_toks:
            return 0.65
        o_toks = set(_tokens(ot))
        coverage = len(q_toks & o_toks) / len(q_toks)
        return 1.0 / (1.0 + 2.2 * coverage)

    idx = ot.find(phrase)
    span = max(len(ot), 1)
    early = idx / span
    brevity = min(len(ot), 500) / 500.0
    specificity = min(len(phrase), 48) / 48.0
    if primary:
        return 0.04 + 0.10 * early + 0.05 * brevity
    return 0.13 + 0.10 * early + 0.05 * brevity - 0.03 * specificity


def _best_matched_phrase(
    oracle_text: str, phrases: list[str], query: str = ""
) -> str | None:
    ot = (oracle_text or "").lower()
    q = " ".join((query or "").lower().split())
    # Prefer the user's exact query string when it appears in oracle.
    if q and q in ot:
        return q
    for phrase in sorted(phrases, key=len, reverse=True):
        if phrase in ot:
            return phrase
    return None


def engine_rank(type_line: str = "", oracle_text: str = "") -> int:
    """Tie-break priority for card-advantage hits (lower = better)."""
    ot = (oracle_text or "").lower()
    tl = (type_line or "").lower()
    if "at the beginning of your upkeep" in ot and "draw" in ot:
        return 0
    if "at the beginning of your first main" in ot and "draw" in ot:
        return 0
    if "whenever" in ot and "draw" in ot and (
        "dies" in ot or "die," in ot or "creatures die" in ot
    ):
        return 0
    if "indestructible" in ot and "draw a card for each" in ot:
        return 0
    if "skip your draw step" in ot or (
        "exile the top card of your library" in ot and "pay 1 life" in ot
    ):
        return 1
    if ("enchantment" in tl or "planeswalker" in tl) and "draw" in ot:
        return 1
    if ("instant" in tl or "sorcery" in tl) and "draw two cards" in ot:
        return 2
    if "draw a card for each" in ot:
        return 2
    return 3


def hit_sort_key(hit: dict) -> tuple:
    """Stable ranking: distance, then engines / short oracle, then name."""
    source = hit.get("source")
    # Missing source = pure lexical list before merge; treat as lexical.
    source_rank = 0 if source in (None, "lexical", "hybrid") else 1
    return (
        float(hit.get("distance") if hit.get("distance") is not None else 99.0),
        source_rank,
        0 if hit.get("_exact_query") else 1,
        engine_rank(hit.get("type_line") or "", hit.get("oracle_text") or ""),
        len(hit.get("oracle_text") or ""),
        hit.get("name") or "",
    )


def _draw_relevance_delta(type_line: str, oracle: str, oracle_compact: str) -> float:
    """Signed distance adjustment for draw-family queries (negative = better)."""
    tl = type_line
    ot = oracle
    oc = oracle_compact
    delta = 0.0

    # Repeatable engines first.
    if "at the beginning of your upkeep" in ot and "draw" in ot:
        delta -= 0.085
    elif "at the beginning of your first main" in ot and "draw a card" in ot:
        delta -= 0.08
    elif "whenever" in ot and "draw" in ot and (
        "dies" in ot or "die," in ot or "creatures die" in ot
    ):
        delta -= 0.08
    elif ("enchantment" in tl or "planeswalker" in tl) and "draw" in ot:
        delta -= 0.045

    # Necro-style impulse / skip-draw.
    if "skip your draw step" in ot:
        delta -= 0.07
    if "exile the top card of your library" in ot and (
        "hand" in ot or "pay 1 life" in ot
    ):
        delta -= 0.065

    # The One Ring / scaling draw.
    if "indestructible" in ot and "draw a card for each" in ot:
        delta -= 0.09
    elif "draw a card for each" in ot:
        delta -= 0.04

    # BMC-style treasure + draw modal.
    if "draw a card" in ot and "treasure" in ot:
        delta -= 0.055

    # Classic black burst draw (Night's Whisper, Village Rites, Dispute).
    # Require the compact text (no reminder) so "draw two instead" on activated
    # creatures does not count as a burst-draw spell.
    if ("instant" in tl or "sorcery" in tl) and "draw two cards" in oc and len(oc) < 220:
        delta -= 0.075
        if "lose" in oc and "life" in oc:
            delta -= 0.025
        if "sacrifice" in oc and (
            "additional cost" in oc or "as an additional cost" in oc
        ):
            # Village Rites / Corrupted Conviction: preamble pushes phrase late.
            delta -= 0.055
        elif "sacrifice" in oc and "treasure" in oc:
            delta -= 0.035
    elif "draw two cards" in oc and "sacrifice" in oc and len(oc) < 220:
        if "instant" in tl or "sorcery" in tl:
            delta -= 0.05

    # Demote activated creature cantrips for "draw a card" (Agency Coroner).
    if "creature" in tl and ":" in ot and "draw a card" in ot:
        if "whenever" not in ot and "at the beginning" not in ot:
            delta += 0.04

    return delta


def merge_hit_maps(
    embedding_hits: list[dict],
    lexical_hits: list[dict],
    *,
    lexical_weight: float = 0.55,
    embedding_weight: float = 0.45,
) -> list[dict]:
    """Merge by card name. Lexical-only and embedding-only both survive; both → blend."""
    by_key: dict[str, dict] = {}

    def key(name: str) -> str:
        return name.lower()

    for hit in embedding_hits:
        name = hit.get("name") or ""
        if not name:
            continue
        k = key(name)
        by_key[k] = {
            **hit,
            "_emb_distance": float(hit.get("distance") if hit.get("distance") is not None else 1.0),
            "_lex_distance": None,
            "source": "embedding",
        }

    for hit in lexical_hits:
        name = hit.get("name") or ""
        if not name:
            continue
        k = key(name)
        lex_d = float(hit.get("distance") if hit.get("distance") is not None else 0.5)
        if k in by_key:
            prev = by_key[k]
            prev["_lex_distance"] = lex_d
            emb_d = prev["_emb_distance"]
            emb_s = 1.0 / (1.0 + emb_d)
            lex_s = 1.0 / (1.0 + lex_d)
            blended = lexical_weight * lex_s + embedding_weight * emb_s
            blended_d = (1.0 / blended) - 1.0
            # Never punish a strong lexical hit with a mediocre ANN neighbor.
            prev["distance"] = min(lex_d, emb_d, blended_d)
            prev["source"] = "hybrid"
            for field in (
                "matched_phrase",
                "text",
                "type_line",
                "oracle_text",
                "_exact_query",
            ):
                if hit.get(field) is not None:
                    prev[field] = hit[field]
        else:
            by_key[k] = {
                **hit,
                "_emb_distance": None,
                "_lex_distance": lex_d,
                "distance": lex_d,
                "source": "lexical",
            }

    merged = list(by_key.values())
    merged.sort(key=hit_sort_key)
    for hit in merged:
        hit.pop("_emb_distance", None)
        hit.pop("_lex_distance", None)
        hit.pop("_exact_query", None)
    return merged


def sql_identity_clause(allowed_colors: list[str] | None) -> tuple[str, list]:
    """Cheap JSON color_identity ⊆ allowed. Colorless always passes."""
    allowed = set(allowed_colors or [])
    # Reject rows that contain any color letter outside the identity.
    forbidden = [c for c in ("W", "U", "B", "R", "G") if c not in allowed]
    if not forbidden:
        return "1=1", []
    clauses = []
    params: list = []
    for color in forbidden:
        # color_identity is a JSON list like ["B"] or ["U","B"].
        clauses.append("color_identity NOT LIKE ?")
        params.append(f'%"{color}"%')
    return "(" + " AND ".join(clauses) + ")", params


def lexical_search_sqlite(
    conn: sqlite3.Connection,
    query: str,
    allowed_colors: list[str] | None,
    *,
    limit: int = 120,
    cmc_min: float | None = None,
    cmc_max: float | None = None,
) -> list[dict]:
    """Substring oracle search with identity / CMC filters. No embeddings.

    Runs the user query as its own LIKE first, then family expansions, so a
    broad OR ... LIMIT N cannot drop Phyrexian Arena behind alphabetical noise.
    """
    phrases = lexical_phrases(query)
    if not phrases:
        return []

    identity_sql, identity_params = sql_identity_clause(allowed_colors)
    cmc_sql = ""
    cmc_params: list = []
    if cmc_min is not None:
        cmc_sql += " AND cmc >= ?"
        cmc_params.append(float(cmc_min))
    if cmc_max is not None:
        cmc_sql += " AND cmc <= ?"
        cmc_params.append(float(cmc_max))

    base_sql = (
        "SELECT name, type_line, oracle_text, color_identity, cmc, legalities, "
        "price_usd, price_eur, keywords "
        "FROM cards WHERE lower(oracle_text) LIKE ? AND "
        f"{identity_sql} AND type_line NOT LIKE '%Basic Land%' {cmc_sql}"
    )

    # Query string first (most important), then other phrases.
    q_norm = " ".join((query or "").lower().split())
    ordered_phrases: list[str] = []
    if q_norm:
        ordered_phrases.append(q_norm)
    for phrase in phrases:
        if phrase not in ordered_phrases:
            ordered_phrases.append(phrase)

    allowed = set(allowed_colors or [])
    by_name: dict[str, dict] = {}
    q_l = (query or "").lower()

    for phrase in ordered_phrases:
        # Never LIMIT the user query or core draw templates — a random LIMIT
        # was dropping Village Rites / Corrupted Conviction while keeping noise.
        params = [f"%{phrase}%"] + identity_params + cmc_params
        if phrase == q_norm or phrase in _DRAW_CORE_PHRASES:
            rows = conn.execute(base_sql, params).fetchall()
        else:
            rows = conn.execute(base_sql + " LIMIT 500", params).fetchall()
        for name, type_line, oracle_text, color_identity, cmc, legalities, *_rest in rows:
            key = name.lower()
            if key in by_name:
                continue
            if not is_searchable_card(name, type_line, legalities):
                continue
            try:
                colors = set(json.loads(color_identity or "[]"))
            except json.JSONDecodeError:
                colors = set()
            if not colors.issubset(allowed):
                continue
            matched = _best_matched_phrase(oracle_text or "", phrases, query)
            dist = lexical_distance(query, oracle_text or "", matched)
            tl = (type_line or "").lower()
            ot_l = (oracle_text or "").lower()
            # Strip reminder text so "draw two cards instead" does not look like
            # a primary Village Rites-style spell (Agency Coroner).
            ot_compact = re.sub(r"\([^)]*\)", "", ot_l)
            ot_compact = re.sub(r"\s+", " ", ot_compact).strip()
            draw_query = any(t in q_l for t in _FAMILY_TRIGGERS["draw"])
            if draw_query:
                dist += _draw_relevance_delta(tl, ot_l, ot_compact)
            if any(k in q_l for k in ("engine", "repeatable", "advantage", "upkeep")):
                if "enchantment" in tl or "planeswalker" in tl or "artifact" in tl:
                    dist -= 0.02
                elif "instant" in tl or "sorcery" in tl:
                    # Keep classic burst draw; demote unrelated instants only.
                    if not ("draw two cards" in ot_compact and len(ot_compact) < 220):
                        dist += 0.015
            if matched in _DRAW_CORE_PHRASES and len(ot_compact) < 180:
                dist -= 0.01
            if q_norm and matched == q_norm:
                dist -= 0.01
            by_name[key] = {
                "name": name,
                "text": card_document(type_line, oracle_text),
                # No hard floor: floor ties made alphabetical junk crowd Arena.
                "distance": max(0.001, dist),
                "matched_phrase": matched,
                "cmc": cmc,
                "type_line": type_line,
                "oracle_text": oracle_text,
                "legalities": legalities,
                "_exact_query": bool(q_norm and q_norm in ot_l),
            }

    hits = list(by_name.values())
    hits.sort(key=hit_sort_key)
    for hit in hits:
        hit.pop("_exact_query", None)
    return hits[:limit]
