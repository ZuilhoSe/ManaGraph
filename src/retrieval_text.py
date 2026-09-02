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
    # Interaction is split so "counter target spell" does not expand into
    # destroy/exile noise (and vice versa).
    "counter": (
        "counterspell",
        "counterspells",
        "counter target",
        "counter a",
        "permission",
    ),
    "removal": (
        "removal",
        "destroy target",
        "exile target",
        "board wipe",
        "wrath",
    ),
    "bounce": (
        "bounce",
        "return target",
        "return to owner",
    ),
    "enchantment": (
        "enchantress",
        "enchantment spell",
        "cast an enchantment",
        "enchantments matter",
        "constellation",
        "eerie",
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
        "draw an additional card",
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
    "counter": (
        "counter target spell",
        "counter target noncreature spell",
        "counter target instant or sorcery spell",
        "counter target creature spell",
        "counter target activated",
        "counter target triggered",
        "counter target",
    ),
    "removal": (
        "destroy target creature",
        "destroy target permanent",
        "exile target creature",
        "exile target permanent",
        "destroy all creatures",
        "fight target",
    ),
    "bounce": (
        "return target",
        "return it to its owner's hand",
        "owner's hand",
    ),
    "enchantment": (
        "whenever you cast an enchantment spell",
        "whenever you cast an enchantment",
        "cast an enchantment spell",
        "enchantment you control enters",
        "an enchantment you control enters",
        "whenever an enchantment you control",
        "enchantment enters the battlefield",
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
        "draw an additional card",
        "you may draw",
        "you draw a card",
    }
)

_COUNTER_CORE_PHRASES = frozenset(
    {
        "counter target spell",
        "counter target noncreature spell",
        "counter target instant or sorcery spell",
        "counter target creature spell",
        "counter target",
    }
)

_ENCHANTMENT_CORE_PHRASES = frozenset(
    {
        "whenever you cast an enchantment spell",
        "whenever you cast an enchantment",
        "cast an enchantment spell",
        "enchantment you control enters",
        "whenever an enchantment you control",
    }
)

# Phrases that must never be SQL LIMIT'd (same trap as draw staples).
_UNLIMITED_CORE_PHRASES = (
    _DRAW_CORE_PHRASES | _COUNTER_CORE_PHRASES | _ENCHANTMENT_CORE_PHRASES
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

    matched_families: set[str] = set()
    for family, triggers in _FAMILY_TRIGGERS.items():
        if any(t in q for t in triggers):
            matched_families.add(family)
            for phrase in _FAMILY_PHRASES.get(family, ()):
                add(phrase)

    # Bare "interaction" (or "interact") should cover permission + removal + bounce.
    if "interaction" in q or re.search(r"\binteract\b", q):
        for family in ("counter", "removal", "bounce"):
            if family in matched_families:
                continue
            matched_families.add(family)
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
    # Family cores count as primary when the query is in that family.
    if phrase in _DRAW_CORE_PHRASES and any(t in q for t in _FAMILY_TRIGGERS["draw"]):
        return True
    if phrase in _COUNTER_CORE_PHRASES and any(
        t in q for t in _FAMILY_TRIGGERS["counter"]
    ):
        return True
    if phrase in _ENCHANTMENT_CORE_PHRASES and any(
        t in q for t in _FAMILY_TRIGGERS["enchantment"]
    ):
        return True
    return False


def lexical_distance(query: str, oracle_text: str, matched_phrase: str | None = None) -> float:
    """Chroma-compatible distance (lower = better) for a lexical hit.

    Primary matches (the query string, or core family templates on a matching
    query) outrank looser family expansions like impulse-draw / look-at-top.
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
        for core in _UNLIMITED_CORE_PHRASES:
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


def role_rank(query: str = "", type_line: str = "", oracle_text: str = "") -> int:
    """Tie-break priority for the active query family (lower = better)."""
    q = (query or "").lower()
    ot = (oracle_text or "").lower()
    tl = (type_line or "").lower()

    if any(t in q for t in _FAMILY_TRIGGERS["counter"]) or "counter target" in q:
        if "counter target" in ot and ("instant" in tl or "sorcery" in tl):
            # Hard counters that lead with the effect beat long modal text.
            head = ot[:80]
            if "counter target spell" in head or ot.startswith("counter target"):
                return 0
            if "counter target noncreature" in head or "counter target instant" in head:
                return 0
            if "you draw a card" in ot and "counter target spell" in ot:
                return 0
            return 1
        return 3

    if any(t in q for t in _FAMILY_TRIGGERS["enchantment"]):
        if "cast an enchantment" in ot and "draw" in ot:
            return 0
        if "enchantment you control enters" in ot and "draw" in ot:
            return 0
        if "enchantment you control" in ot and "draw" in ot:
            return 1
        return 3

    if any(t in q for t in _FAMILY_TRIGGERS["draw"]):
        return engine_rank(type_line, oracle_text)

    return 3


def engine_rank(type_line: str = "", oracle_text: str = "") -> int:
    """Tie-break priority for card-advantage hits (lower = better)."""
    ot = (oracle_text or "").lower()
    tl = (type_line or "").lower()
    if "at the beginning of your upkeep" in ot and "draw" in ot:
        return 0
    if "at the beginning of your draw step" in ot and "draw" in ot:
        return 0
    if "at the beginning of your first main" in ot and "draw" in ot:
        return 0
    if "combat damage" in ot and "draw a card" in ot:
        return 1
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
    """Stable ranking: distance, then role / engines / short oracle, then name."""
    source = hit.get("source")
    # Missing source = pure lexical list before merge; treat as lexical.
    # Ontology hits are mechanic matches and should compete with lexical.
    source_rank = 0 if source in (None, "lexical", "hybrid", "ontology") else 1
    role = hit.get("_role_rank")
    if role is None:
        role = role_rank(
            hit.get("_query") or "",
            hit.get("type_line") or "",
            hit.get("oracle_text") or "",
        )
    oracle = hit.get("oracle_text") or ""
    compact = re.sub(r"\([^)]*\)", "", oracle)
    compact = re.sub(r"\s+", " ", compact).strip()
    return (
        float(hit.get("distance") if hit.get("distance") is not None else 99.0),
        source_rank,
        0 if hit.get("_exact_query") else 1,
        int(role),
        # Higher boost (from relevance deltas) ranks first when distance ties.
        -float(hit.get("_boost") or 0.0),
        len(compact),
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
    elif "at the beginning of your draw step" in ot and "draw" in ot:
        delta -= 0.08
    elif "at the beginning of your first main" in ot and "draw a card" in ot:
        delta -= 0.08
    elif "whenever" in ot and "draw" in ot and (
        "dies" in ot or "die," in ot or "creatures die" in ot
    ):
        delta -= 0.08
    elif "combat damage" in ot and "draw" in ot:
        delta -= 0.055
    elif ("enchantment" in tl or "planeswalker" in tl) and "draw" in ot:
        delta -= 0.045

    if "draw an additional card" in ot:
        delta -= 0.05

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
            # Village Rites / Corrupted Conviction / Deadly Dispute.
            delta -= 0.06
        if "treasure" in oc:
            delta -= 0.04
    elif "draw two cards" in oc and "sacrifice" in oc and len(oc) < 220:
        if "instant" in tl or "sorcery" in tl:
            delta -= 0.055
            if "treasure" in oc:
                delta -= 0.03

    # Demote activated creature cantrips for "draw a card" (Agency Coroner).
    if "creature" in tl and ":" in ot and "draw a card" in ot:
        if "whenever" not in ot and "at the beginning" not in ot:
            delta += 0.04

    return delta


def _counter_relevance_delta(type_line: str, oracle: str, oracle_compact: str) -> float:
    """Boost hard permission; demote soft/long counter text."""
    tl = type_line
    ot = oracle
    oc = oracle_compact
    delta = 0.0
    if "counter target" not in ot:
        return 0.02
    if "instant" in tl or "sorcery" in tl:
        delta -= 0.06
        if oc.startswith("counter target") or "counter target spell." in oc[:60]:
            delta -= 0.05
        # Variant staples (Negate / Veto / Muddle) must not lose to Cancel clones.
        if "counter target noncreature spell" in oc:
            delta -= 0.045
        if "counter target instant or sorcery" in oc:
            delta -= 0.045
        if "can't be countered" in ot:
            delta -= 0.035
        if "draw" in ot and "counter target" in ot:
            # Arcane Denial-style: real counter that also replaces itself.
            delta -= 0.04
            if "you draw a card" in ot:
                delta -= 0.03
        if len(oc) < 100:
            delta -= 0.03
        elif len(oc) < 160:
            delta -= 0.015
        elif len(oc) > 280:
            delta += 0.025
    else:
        delta += 0.03
    return delta


def diversify_by_phrase(
    hits: list[dict],
    limit: int,
    *,
    prefer_phrase: str | None = None,
) -> list[dict]:
    """Round-robin across matched_phrase buckets so one template cannot fill top-N.

    Hits without a lexical matched_phrase (pure embedding) are appended only
    after phrase buckets are exhausted. Optionally reserve early slots for the
    user's primary phrase (e.g. \"counter target spell\") so Counterspell is not
    crowded out by Negate/Muddle diversity alone.
    """
    if limit <= 0 or len(hits) <= limit:
        return hits[:limit]
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    other: list[dict] = []
    for hit in hits:
        phrase = (hit.get("matched_phrase") or "").lower().strip()
        if not phrase:
            other.append(hit)
            continue
        if phrase not in buckets:
            buckets[phrase] = []
            order.append(phrase)
        buckets[phrase].append(hit)

    out: list[dict] = []
    prefer = " ".join((prefer_phrase or "").lower().split())
    if prefer and prefer in buckets:
        reserved = min(len(buckets[prefer]), max(limit // 3, 10))
        out.extend(buckets[prefer][:reserved])
        buckets[prefer] = buckets[prefer][reserved:]

    while len(out) < limit:
        progressed = False
        for key in order:
            if buckets[key] and len(out) < limit:
                out.append(buckets[key].pop(0))
                progressed = True
        if not progressed:
            break
    for hit in other:
        if len(out) >= limit:
            break
        out.append(hit)
    return out


def _enchantment_relevance_delta(type_line: str, oracle: str) -> float:
    """Boost enchantress / constellation / eerie draw payoffs."""
    ot = oracle
    delta = 0.0
    if "cast an enchantment" in ot and "draw" in ot:
        delta -= 0.08
    if "enchantment you control enters" in ot and "draw" in ot:
        delta -= 0.075
    if "enchantment you control" in ot and "graveyard" in ot and "draw" in ot:
        delta -= 0.07
    if "enchantment" in ot and "draw" in ot and "creature" in (type_line or "").lower():
        delta -= 0.02
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
            "source": hit.get("source") or "embedding",
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
                "_query",
                "_role_rank",
                "_boost",
            ):
                if hit.get(field) is not None:
                    prev[field] = hit[field]
        else:
            by_key[k] = {
                **hit,
                "_emb_distance": None,
                "_lex_distance": lex_d,
                "distance": lex_d,
                "source": hit.get("source") or "lexical",
            }

    merged = list(by_key.values())
    merged.sort(key=hit_sort_key)
    for hit in merged:
        hit.pop("_emb_distance", None)
        hit.pop("_lex_distance", None)
        hit.pop("_exact_query", None)
        # Keep _query / _role_rank for hit_sort_key re-sorts in hybrid_search.
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


def _collect_ordered_lexical_phrases(
    query: str,
    extra_phrases: list[str] | None = None,
) -> list[str]:
    """User query first, then its family expansions, then extra harness phrases.

    One ordered LIKE loop — extras do not run as separate full-table searches.
    """
    q_norm = " ".join((query or "").lower().split())
    ordered: list[str] = []
    seen: set[str] = set()

    def add(phrase: str) -> None:
        p = " ".join((phrase or "").lower().split())
        if not p or p in seen:
            return
        seen.add(p)
        ordered.append(p)

    if q_norm:
        add(q_norm)
    for phrase in lexical_phrases(query):
        add(phrase)
    for extra in extra_phrases or []:
        extra_n = " ".join((extra or "").lower().split())
        if extra_n:
            add(extra_n)
        for phrase in lexical_phrases(extra):
            add(phrase)
    return ordered


def _score_query_for_oracle(
    oracle_text: str,
    query: str,
    extra_phrases: list[str] | None,
) -> str:
    """Prefer the user query when it appears; otherwise a matching extra phrase."""
    ot = (oracle_text or "").lower()
    q_norm = " ".join((query or "").lower().split())
    if q_norm and q_norm in ot:
        return query
    for extra in extra_phrases or []:
        extra_n = " ".join((extra or "").lower().split())
        if extra_n and extra_n in ot:
            return extra
    return query


def lexical_search_sqlite(
    conn: sqlite3.Connection,
    query: str,
    allowed_colors: list[str] | None,
    *,
    limit: int = 120,
    cmc_min: float | None = None,
    cmc_max: float | None = None,
    extra_phrases: list[str] | None = None,
) -> list[dict]:
    """Substring oracle search with identity / CMC filters. No embeddings.

    Runs the user query as its own LIKE first, then family expansions, then
    extra harness phrases in the same scan so a broad OR ... LIMIT N cannot
    drop Phyrexian Arena behind alphabetical noise.
    """
    ordered_phrases = _collect_ordered_lexical_phrases(query, extra_phrases)
    if not ordered_phrases:
        return []
    phrases = list(ordered_phrases)

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

    q_norm = " ".join((query or "").lower().split())
    extra_norms = {
        " ".join((extra or "").lower().split())
        for extra in (extra_phrases or [])
        if extra
    }
    allowed = set(allowed_colors or [])
    by_name: dict[str, dict] = {}

    for phrase in ordered_phrases:
        # Never LIMIT the user query or core family templates — a random LIMIT
        # was dropping Village Rites / Negate / Mesa Enchantress behind noise.
        params = [f"%{phrase}%"] + identity_params + cmc_params
        if (
            phrase == q_norm
            or phrase in extra_norms
            or phrase in _UNLIMITED_CORE_PHRASES
        ):
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
            score_query = _score_query_for_oracle(
                oracle_text or "", query, extra_phrases
            )
            matched = _best_matched_phrase(oracle_text or "", phrases, score_query)
            dist = lexical_distance(score_query, oracle_text or "", matched)
            tl = (type_line or "").lower()
            ot_l = (oracle_text or "").lower()
            # Strip reminder text so "draw two cards instead" does not look like
            # a primary Village Rites-style spell (Agency Coroner).
            ot_compact = re.sub(r"\([^)]*\)", "", ot_l)
            ot_compact = re.sub(r"\s+", " ", ot_compact).strip()
            q_l = (score_query or "").lower()
            score_norm = " ".join(q_l.split())

            boost = 0.0
            if any(t in q_l for t in _FAMILY_TRIGGERS["draw"]):
                dlt = _draw_relevance_delta(tl, ot_l, ot_compact)
                dist += dlt
                boost -= dlt
            if any(t in q_l for t in _FAMILY_TRIGGERS["counter"]) or "counter target" in q_l:
                dlt = _counter_relevance_delta(tl, ot_l, ot_compact)
                dist += dlt
                boost -= dlt
            if any(t in q_l for t in _FAMILY_TRIGGERS["enchantment"]):
                dlt = _enchantment_relevance_delta(tl, ot_l)
                dist += dlt
                boost -= dlt
            if any(k in q_l for k in ("engine", "repeatable", "advantage", "upkeep")):
                if "enchantment" in tl or "planeswalker" in tl or "artifact" in tl:
                    dist -= 0.02
                    boost += 0.02
                elif "instant" in tl or "sorcery" in tl:
                    # Keep classic burst draw; demote unrelated instants only.
                    if not ("draw two cards" in ot_compact and len(ot_compact) < 220):
                        if "counter target" not in ot_l:
                            dist += 0.015
                            boost -= 0.015
            if matched in _UNLIMITED_CORE_PHRASES and len(ot_compact) < 180:
                dist -= 0.01
                boost += 0.01
            if score_norm and matched == score_norm:
                dist -= 0.01
                boost += 0.01
            # Core family templates (Negate's "noncreature", Mesa Enchantress, …)
            # must tie with literal query matches, or Cancel-clones bury them.
            exactish = bool(score_norm and score_norm in ot_l) or (
                matched in _UNLIMITED_CORE_PHRASES
                and _is_primary_match(score_query, oracle_text or "", matched)
            )
            by_name[key] = {
                "name": name,
                "text": card_document(type_line, oracle_text),
                # Soft floor keeps merge comparable to Chroma; _boost breaks ties.
                "distance": max(0.001, dist),
                "matched_phrase": matched,
                "cmc": cmc,
                "type_line": type_line,
                "oracle_text": oracle_text,
                "legalities": legalities,
                "_exact_query": exactish,
                "_query": score_query,
                "_role_rank": role_rank(score_query, type_line, oracle_text),
                "_boost": boost,
            }

    hits = list(by_name.values())
    hits.sort(key=hit_sort_key)
    for hit in hits:
        hit.pop("_exact_query", None)
    # Counter / removal queries: diversify templates so Negate/Muddle survive
    # a sea of "Counter target spell." reprints inside limit.
    q_l = (query or "").lower()
    if any(t in q_l for t in _FAMILY_TRIGGERS["counter"]) or "counter target" in q_l:
        return diversify_by_phrase(hits, limit, prefer_phrase=q_norm)
    return hits[:limit]
