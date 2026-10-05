"""Flatten Forge ontology candidates into a searchable predicate index."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping

from ontology.patterns import extract_card_predicates
from retrieval_text import card_document, is_searchable_card
from symbolic_cards import FAMILY_SEARCH_QUERIES, requirement_families

_PREDICATES = frozenset(
    {
        "produces",
        "consumes",
        "emits",
        "rewards",
        "enables",
        "answers",
        "tutors",
        "recurs",
        "protects",
        "requires",
    }
)
_EXPLICIT_RE = re.compile(
    r"\b(" + "|".join(sorted(_PREDICATES)) + r")[=:](?:([a-z0-9_]+)[=:])?([a-z0-9_]+)\b",
    re.IGNORECASE,
)
_CMC_RE = re.compile(r"\bcmc\s*(<=|>=|<|>|=)\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_FILTER_ARG_KEY = {
    "emits": "event",
    "rewards": "event",
    "produces": "object",
    "consumes": "object",
    "enables": "capability",
    "answers": "threat_class",
    "tutors": "selector",
    "protects": "target_class",
    "recurs": "zone_from",
    "requires": "precondition",
}

# Natural-language / family cues (EN + PT). Values are ontology args, never card names.
# Extra turn is not extra combat — those phrases are omitted on purpose.
_NL_PATTERNS: tuple[tuple[tuple[str, ...], str, str, str, float], ...] = (
    (
        (
            "extra combat",
            "additional combat",
            "multiple combat",
            "extra combats",
            "combat turns",
            "combate extra",
            "combates extras",
            "combate adicional",
            "combates adicionais",
            "fase de combate extra",
        ),
        "enables",
        "capability",
        "extra_combat",
        0.10,
    ),
    (
        (
            "sac outlet",
            "sacrifice outlet",
            "saida de sacrificio",
            "saída de sacrifício",
            "outlet de sacrificio",
            "outlet de sacrifício",
        ),
        "enables",
        "capability",
        "sac_outlet",
        0.10,
    ),
    (
        (
            "etb payoff",
            "when a creature enters",
            "creature enters",
            "pago de etb",
            "payoff de etb",
            "quando uma criatura entra",
            "quando entra no campo",
        ),
        "rewards",
        "event",
        "etb",
        0.12,
    ),
    (
        (
            "counterspell",
            "counterspells",
            "counter target spell",
            "contramagica",
            "contramágica",
            "anular magia",
            "anula magia",
        ),
        "answers",
        "threat_class",
        "stack",
        0.12,
    ),
    (
        (
            "card advantage",
            "card draw",
            "draw engine",
            "draw a card",
            "comprar carta",
            "comprar cartas",
            "compra de cartas",
            "vantagem de cartas",
        ),
        "produces",
        "object",
        "card_in_hand",
        0.20,
    ),
    (
        (
            "mana rock",
            "add mana",
            "acceleration",
            "aceleracao de mana",
            "aceleração de mana",
            "pedra de mana",
        ),
        "produces",
        "object",
        "mana",
        0.20,
    ),
    (
        (
            "hexproof",
            "indestructible",
            "protection from",
            "shroud",
            "protecao",
            "proteção",
            "indestrutivel",
            "indestrutível",
        ),
        "protects",
        "",
        "",
        0.12,
    ),
    (
        (
            "reanimate",
            "reanimation",
            "from your graveyard",
            "reanimar",
            "reanimacao",
            "reanimação",
        ),
        "recurs",
        "zone_from",
        "graveyard",
        0.12,
    ),
    (
        ("go wide", "create a token", "creature token", "fichas", "criar ficha"),
        "produces",
        "object",
        "token",
        0.12,
    ),
    (
        ("board wipe", "limpeza de mesa"),
        "answers",
        "threat_class",
        "board",
        0.12,
    ),
    (
        (
            "haste",
            "grant haste",
            "gains haste",
            "gain haste",
            "celeridade",
        ),
        "enables",
        "capability",
        "haste_grant",
        0.12,
    ),
    (
        (
            "convoke",
            "delve",
            "improvise",
            "convocar",
            "improvisar",
        ),
        "enables",
        "capability",
        "convoke_like",
        0.12,
    ),
    (
        (
            "cost reduction",
            "affinity",
            "this spell costs",
            "spells you cast cost",
            "reducao de custo",
            "redução de custo",
            "afinidade",
        ),
        "enables",
        "capability",
        "cost_reduction",
        0.12,
    ),
    (
        ("delirium", "delirio", "delírio"),
        "requires",
        "precondition",
        "delirium",
        0.12,
    ),
    (
        ("threshold", "limiar"),
        "requires",
        "precondition",
        "threshold",
        0.12,
    ),
    (
        ("hellbent", "obstinado"),
        "requires",
        "precondition",
        "hellbent",
        0.12,
    ),
    (
        ("metalcraft", "metalurgia"),
        "requires",
        "precondition",
        "metalcraft",
        0.12,
    ),
    (
        (
            "bounce",
            "devolver para a mao",
            "devolver para a mão",
            "devolucao",
            "devolução",
        ),
        "emits",
        "event",
        "bounce",
        0.12,
    ),
)

_ORACLE_PHRASE_CAP = 6
_EXTRA_TURN_RE = re.compile(
    r"\b(?:extra|additional|another)\s+turns?\b|turno extra|turnos extras",
    re.IGNORECASE,
)
_COMBAT_RE = re.compile(r"\bcombat|combate", re.IGNORECASE)


@dataclass(frozen=True)
class OntologyClause:
    predicate: str
    arg_value: str | None = None
    arg_key: str | None = None
    distance: float = 0.12
    origin: str = "nl"


@dataclass(frozen=True)
class SearchIntent:
    query: str
    clauses: list[OntologyClause]
    oracle_phrases: list[str]
    families: list[str]
    cmc_min: float | None = None
    cmc_max: float | None = None


def extra_turn_not_combat(text: str) -> bool:
    """True when the query asks for extra turns without extra combat."""
    return bool(_EXTRA_TURN_RE.search(text)) and not bool(_COMBAT_RE.search(text))


def compiled_payload(intent: SearchIntent) -> list[dict[str, str | None]]:
    return [
        {
            "predicate": clause.predicate,
            "arg_key": clause.arg_key,
            "arg_value": clause.arg_value,
            "origin": clause.origin,
        }
        for clause in intent.clauses
    ]


def _clauses_from_families(text: str, families: list[str]) -> list[OntologyClause]:
    """Map every requirement family onto ontology clauses. No card names."""
    mapped = list(families)
    if extra_turn_not_combat(text):
        mapped = [name for name in mapped if name != "extra_combat"]

    clauses: list[OntologyClause] = []
    for family in mapped:
        if family == "extra_combat":
            clauses.append(
                OntologyClause(
                    predicate="enables",
                    arg_key="capability",
                    arg_value="extra_combat",
                    distance=0.10,
                    origin="family",
                )
            )
        elif family == "protection":
            if re.search(r"\bcommander\b", text) or "voltron" in text:
                clauses.append(
                    OntologyClause(
                        predicate="protects",
                        arg_key="target_class",
                        arg_value="commander",
                        distance=0.12,
                        origin="family",
                    )
                )
            else:
                clauses.append(
                    OntologyClause(
                        predicate="protects",
                        distance=0.12,
                        origin="family",
                    )
                )
        elif family == "draw":
            clauses.append(
                OntologyClause(
                    predicate="produces",
                    arg_key="object",
                    arg_value="card_in_hand",
                    distance=0.20,
                    origin="family",
                )
            )
        elif family == "ramp":
            clauses.append(
                OntologyClause(
                    predicate="produces",
                    arg_key="object",
                    arg_value="mana",
                    distance=0.20,
                    origin="family",
                )
            )
        elif family == "counter":
            clauses.append(
                OntologyClause(
                    predicate="answers",
                    arg_key="threat_class",
                    arg_value="stack",
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "removal":
            clauses.append(
                OntologyClause(
                    predicate="answers",
                    arg_key="threat_class",
                    arg_value="creature",
                    distance=0.12,
                    origin="family",
                )
            )
            if re.search(r"\bwipe\b", text) or "board wipe" in text or "limpeza de mesa" in text:
                clauses.append(
                    OntologyClause(
                        predicate="answers",
                        arg_key="threat_class",
                        arg_value="board",
                        distance=0.12,
                        origin="family",
                    )
                )
        elif family == "tutor":
            selector = (
                "creature"
                if "creature tutor" in text or "tutor de criatura" in text
                else "any"
            )
            clauses.append(
                OntologyClause(
                    predicate="tutors",
                    arg_key="selector",
                    arg_value=selector,
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "token_engine":
            clauses.append(
                OntologyClause(
                    predicate="produces",
                    arg_key="object",
                    arg_value="token",
                    distance=0.12,
                    origin="family",
                )
            )
            clauses.append(
                OntologyClause(
                    predicate="emits",
                    arg_key="event",
                    arg_value="token_created",
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "graveyard":
            clauses.append(
                OntologyClause(
                    predicate="recurs",
                    arg_key="zone_from",
                    arg_value="graveyard",
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "sacrifice":
            clauses.append(
                OntologyClause(
                    predicate="enables",
                    arg_key="capability",
                    arg_value="sac_outlet",
                    distance=0.10,
                    origin="family",
                )
            )
            asks_outlet = "outlet" in text or "saida" in text or "saída" in text
            asks_sacrifice = bool(re.search(r"sacrif", text)) or "aristocrats" in text
            if asks_sacrifice and not asks_outlet:
                clauses.append(
                    OntologyClause(
                        predicate="consumes",
                        arg_key="object",
                        arg_value="creature",
                        distance=0.12,
                        origin="family",
                    )
                )
                clauses.append(
                    OntologyClause(
                        predicate="rewards",
                        arg_key="event",
                        arg_value="death",
                        distance=0.12,
                        origin="family",
                    )
                )
        elif family == "evasion":
            clauses.append(
                OntologyClause(
                    predicate="enables",
                    arg_key="capability",
                    arg_value="keyword_grant",
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "haste":
            clauses.append(
                OntologyClause(
                    predicate="enables",
                    arg_key="capability",
                    arg_value="haste_grant",
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "convoke":
            clauses.append(
                OntologyClause(
                    predicate="enables",
                    arg_key="capability",
                    arg_value="convoke_like",
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "cost_reduction":
            clauses.append(
                OntologyClause(
                    predicate="enables",
                    arg_key="capability",
                    arg_value="cost_reduction",
                    distance=0.12,
                    origin="family",
                )
            )
        elif family in ("delirium", "threshold", "hellbent", "metalcraft"):
            clauses.append(
                OntologyClause(
                    predicate="requires",
                    arg_key="precondition",
                    arg_value=family,
                    distance=0.12,
                    origin="family",
                )
            )
        elif family == "bounce":
            clauses.append(
                OntologyClause(
                    predicate="emits",
                    arg_key="event",
                    arg_value="bounce",
                    distance=0.12,
                    origin="family",
                )
            )
    return clauses


def _oracle_harness_phrases(query: str, families: list[str]) -> list[str]:
    """Oracle templates for lexical/embedding harness. Never card names."""
    phrases: list[str] = []
    seen: set[str] = set()

    def add(phrase: str) -> None:
        text = " ".join((phrase or "").split())
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        phrases.append(text)

    add(query)
    for family in families:
        for phrase in FAMILY_SEARCH_QUERIES.get(family, ()):
            add(phrase)
            if len(phrases) >= _ORACLE_PHRASE_CAP:
                return phrases
    return phrases


def parse_cmc_bounds(query: str) -> tuple[float | None, float | None]:
    """Parse `cmc<=3` / `cmc>=2` / `cmc=4` tokens. Tighter bound wins on repeats."""
    cmc_min: float | None = None
    cmc_max: float | None = None
    for match in _CMC_RE.finditer(query or ""):
        op = match.group(1)
        value = float(match.group(2))
        if op == "<=":
            cmc_max = value if cmc_max is None else min(cmc_max, value)
        elif op == ">=":
            cmc_min = value if cmc_min is None else max(cmc_min, value)
        elif op == "<":
            bound = value - 1e-9
            cmc_max = bound if cmc_max is None else min(cmc_max, bound)
        elif op == ">":
            bound = value + 1e-9
            cmc_min = bound if cmc_min is None else max(cmc_min, bound)
        else:
            cmc_min = value if cmc_min is None else max(cmc_min, value)
            cmc_max = value if cmc_max is None else min(cmc_max, value)
    return cmc_min, cmc_max


def merge_cmc_bounds(
    cmc_min: float | None,
    cmc_max: float | None,
    other_min: float | None = None,
    other_max: float | None = None,
) -> tuple[float | None, float | None]:
    """Intersect two inclusive CMC windows. Tighter min / tighter max."""
    mins = [value for value in (cmc_min, other_min) if value is not None]
    maxs = [value for value in (cmc_max, other_max) if value is not None]
    return (max(mins) if mins else None, min(maxs) if maxs else None)


def compile_search_intent(query: str) -> SearchIntent:
    """Compile NL or explicit `predicate:value` / `predicate=value` plus optional `cmc<=N`."""
    return _compile_search_intent_cached(query or "")


@lru_cache(maxsize=512)
def _compile_search_intent_cached(query: str) -> SearchIntent:
    text = " ".join(query.lower().split())
    families = requirement_families(query)
    if extra_turn_not_combat(text):
        families = [name for name in families if name != "extra_combat"]
    cmc_min, cmc_max = parse_cmc_bounds(query)
    return SearchIntent(
        query=query,
        clauses=parse_ontology_query(query),
        oracle_phrases=_oracle_harness_phrases(query, families),
        families=families,
        cmc_min=cmc_min,
        cmc_max=cmc_max,
    )


def is_explicit_predicate_query(query: str) -> bool:
    """True when the query is only explicit `predicate:value` / `predicate=value` plus `cmc` bounds."""
    text = " ".join((query or "").lower().split())
    if not text or not _EXPLICIT_RE.search(text):
        return False
    leftover = _EXPLICIT_RE.sub(" ", text)
    leftover = _CMC_RE.sub(" ", leftover)
    leftover = re.sub(r"[,;|/]+", " ", leftover)
    leftover = " ".join(leftover.split())
    return leftover == ""


def search_route(query: str) -> str:
    """How hybrid search should retrieve: `ontology` (no MiniLM/lexical) or `hybrid`."""
    intent = compile_search_intent(query)
    if is_explicit_predicate_query(query) and intent.clauses:
        return "ontology"
    return "hybrid"


def _as_arg_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).strip()
    return text if text else None


_SKIP_INDEX_KEYS = frozenset({"token_script"})
_COLOR_INDEX_OK = frozenset({"w", "u", "b", "r", "g", "c", "any"})


def _indexable_arg(arg_key: str, value: str) -> bool:
    """Drop leaked Forge DSL that is not a searchable argument."""
    key = str(arg_key or "").lower()
    if key in _SKIP_INDEX_KEYS:
        return False
    if key == "color":
        return value.lower() in _COLOR_INDEX_OK
    if key == "rate":
        try:
            int(value)
        except (TypeError, ValueError):
            return False
        return True
    return True


def flatten_candidates(
    card_id: str | None,
    card_name: str | None,
    candidates: Iterable[Mapping[str, Any]] | None,
) -> list[tuple[str | None, str, str, str, str]]:
    """Turn candidate dicts into ontology_predicates rows. Skip validation_only."""
    rows: list[tuple[str | None, str, str, str, str]] = []
    seen: set[tuple[str | None, str, str, str, str]] = set()
    name = str(card_name or "").strip()
    for item in candidates or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("validation_only"):
            continue
        predicate = str(item.get("predicate") or "").strip()
        if not predicate:
            continue
        arguments = item.get("arguments") or {}
        if not isinstance(arguments, Mapping) or not arguments:
            row = (card_id, name, predicate, "", "")
            if row not in seen:
                seen.add(row)
                rows.append(row)
            continue
        for arg_key, arg_value in arguments.items():
            value = _as_arg_value(arg_value)
            if value is None:
                continue
            if not _indexable_arg(str(arg_key), value):
                continue
            row = (card_id, name, predicate, str(arg_key), value)
            if row not in seen:
                seen.add(row)
                rows.append(row)
    return rows


def predicates_for_names(
    conn: sqlite3.Connection,
    names: Iterable[str],
) -> list[tuple[str, str, str, str]]:
    """Return (card_name, predicate, arg_key, arg_value) for the given names."""
    cleaned = [str(name).strip() for name in names if str(name).strip()]
    if not cleaned:
        return []
    placeholders = ",".join("?" for _ in cleaned)
    try:
        rows = conn.execute(
            f"""
            SELECT card_name, predicate, arg_key, arg_value
              FROM ontology_predicates
             WHERE card_name COLLATE NOCASE IN ({placeholders})
            """,
            cleaned,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        (str(row[0] or ""), str(row[1] or ""), str(row[2] or ""), str(row[3] or ""))
        for row in rows
    ]


def _load_candidates(raw: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, Mapping)]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, Mapping)]


def _merged_candidates(
    stored: Iterable[Mapping[str, Any]] | None,
    *,
    name: str | None = None,
    oracle_text: str | None = None,
    type_line: str | None = None,
    keywords: Any = None,
) -> list[Mapping[str, Any]]:
    merged: list[Mapping[str, Any]] = list(_load_candidates(stored))
    merged.extend(
        extract_card_predicates(
            oracle_text or "",
            name=name or "",
            type_line=type_line or "",
            keywords=keywords,
        )
    )
    return merged


def rebuild_predicate_index(conn: sqlite3.Connection) -> int:
    """Rebuild ontology_predicates from Forge candidates plus Oracle templates.

    ontology_cards is the canonical matched view. forge_records fill in
    matched_card_id rows that are not already indexed. Cards with Oracle text
    and no Forge row still get Tier 2 predicates.
    """
    conn.execute("DELETE FROM ontology_predicates")
    rows: list[tuple[str | None, str, str, str, str]] = []
    indexed_ids: set[str] = set()

    try:
        ontology = conn.execute(
            """
            SELECT o.card_id, o.scryfall_name, o.forge_candidates_json,
                   c.oracle_text, c.type_line, c.keywords, c.name
              FROM ontology_cards o
         LEFT JOIN cards c ON c.id = o.card_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            ontology = conn.execute(
                """
                SELECT card_id, scryfall_name, forge_candidates_json,
                       NULL, NULL, NULL, NULL
                  FROM ontology_cards
                """
            ).fetchall()
        except sqlite3.OperationalError:
            ontology = []
    for row in ontology:
        card_id = row[0]
        name = row[6] or row[1]
        flattened = flatten_candidates(
            card_id,
            name,
            _merged_candidates(
                row[2],
                name=name,
                oracle_text=row[3],
                type_line=row[4],
                keywords=row[5],
            ),
        )
        rows.extend(flattened)
        if card_id:
            indexed_ids.add(card_id)

    try:
        forge = conn.execute(
            """
            SELECT forge_name, matched_card_id, candidates_json
              FROM forge_records
            """
        ).fetchall()
    except sqlite3.OperationalError:
        forge = []
    for name, matched_card_id, candidates_json in forge:
        card_id = matched_card_id or None
        if card_id and card_id in indexed_ids:
            continue
        flattened = flatten_candidates(card_id, name, _load_candidates(candidates_json))
        if not flattened:
            continue
        rows.extend(flattened)
        if card_id:
            indexed_ids.add(card_id)

    try:
        leftover = conn.execute(
            """
            SELECT id, name, oracle_text, type_line, keywords
              FROM cards
            """
        ).fetchall()
    except sqlite3.OperationalError:
        leftover = []
    for card_id, name, oracle_text, type_line, keywords in leftover:
        if card_id in indexed_ids:
            continue
        flattened = flatten_candidates(
            card_id,
            name,
            extract_card_predicates(
                oracle_text or "",
                name=name or "",
                type_line=type_line or "",
                keywords=keywords,
            ),
        )
        if not flattened:
            continue
        rows.extend(flattened)
        if card_id:
            indexed_ids.add(card_id)

    if rows:
        conn.executemany(
            """
            INSERT INTO ontology_predicates
              (card_id, card_name, predicate, arg_key, arg_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def split_filter_values(raw: str) -> list[str]:
    return [
        part.strip().lower()
        for part in str(raw or "").replace(";", ",").split(",")
        if part.strip()
    ]


def clauses_from_filters(filters: Mapping[str, str] | None) -> list[OntologyClause]:
    """AND-filters from search_cards kwargs (`rewards=etb`, comma-separated values)."""
    clauses: list[OntologyClause] = []
    for predicate, raw in (filters or {}).items():
        name = str(predicate or "").strip().lower()
        if name not in _PREDICATES:
            continue
        arg_key = _FILTER_ARG_KEY.get(name)
        for value in split_filter_values(raw):
            clauses.append(
                OntologyClause(
                    predicate=name,
                    arg_key=arg_key,
                    arg_value=value,
                    distance=0.06,
                    origin="filter",
                )
            )
    return _unique_clauses(clauses)


def names_matching_clause(conn: sqlite3.Connection, clause: OntologyClause) -> set[str]:
    """Lowercased card names that carry one predicate clause."""
    if clause.arg_key and clause.arg_value:
        sql = (
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate = ? AND arg_key = ? AND arg_value = ?"
        )
        params: list[Any] = [clause.predicate, clause.arg_key, clause.arg_value]
    elif clause.arg_value:
        sql = (
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate = ? AND arg_value = ?"
        )
        params = [clause.predicate, clause.arg_value]
    elif clause.arg_key:
        sql = (
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate = ? AND arg_key = ?"
        )
        params = [clause.predicate, clause.arg_key]
    else:
        sql = "SELECT DISTINCT card_name FROM ontology_predicates WHERE predicate = ?"
        params = [clause.predicate]
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row[0]).lower() for row in rows if row[0]}


def names_matching_all_clauses(
    conn: sqlite3.Connection,
    clauses: Iterable[OntologyClause],
    candidate_names: Iterable[str] | None = None,
) -> set[str]:
    """Names that satisfy every clause. Empty clauses keep the candidate set."""
    unique = _unique_clauses(list(clauses or []))
    if not unique:
        if candidate_names is None:
            return set()
        return {str(name) for name in candidate_names if str(name).strip()}
    matching: set[str] | None = None
    for clause in unique:
        names = names_matching_clause(conn, clause)
        matching = names if matching is None else matching & names
        if not matching:
            return set()
    allowed = matching or set()
    if candidate_names is None:
        return allowed
    return {str(name) for name in candidate_names if str(name).lower() in allowed}


def filter_hits_by_clauses(
    conn: sqlite3.Connection,
    hits: Iterable[Mapping[str, Any]],
    clauses: Iterable[OntologyClause],
) -> list[dict]:
    """Keep hybrid hits whose names match every filter clause."""
    unique = _unique_clauses(list(clauses or []))
    material = [dict(hit) for hit in hits]
    if not unique:
        return material
    allowed = {
        name.lower()
        for name in names_matching_all_clauses(
            conn,
            unique,
            candidate_names=[str(hit.get("name") or "") for hit in material],
        )
    }
    return [hit for hit in material if str(hit.get("name") or "").lower() in allowed]


def parse_ontology_query(query: str) -> list[OntologyClause]:
    """Parse explicit `predicate:value` / `predicate=value` forms and NL / requirement families."""
    text = " ".join((query or "").lower().split())
    if not text:
        return []

    clauses: list[OntologyClause] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    def add(clause: OntologyClause) -> None:
        key = (clause.predicate, clause.arg_key, clause.arg_value)
        if key in seen:
            return
        seen.add(key)
        clauses.append(clause)

    for match in _EXPLICIT_RE.finditer(text):
        predicate = match.group(1).lower()
        arg_key = (match.group(2) or "").lower() or None
        arg_value = match.group(3).lower()
        add(
            OntologyClause(
                predicate=predicate,
                arg_key=arg_key,
                arg_value=arg_value,
                distance=0.06,
                origin="explicit",
            )
        )

    skip_extra_combat = extra_turn_not_combat(text)
    for triggers, predicate, arg_key, arg_value, distance in _NL_PATTERNS:
        if skip_extra_combat and arg_value == "extra_combat":
            continue
        if any(trigger in text for trigger in triggers):
            add(
                OntologyClause(
                    predicate=predicate,
                    arg_key=arg_key or None,
                    arg_value=arg_value or None,
                    distance=distance,
                    origin="nl",
                )
            )

    # Short family words that would over-fire as substrings of unrelated queries.
    if re.search(r"\bdraw\b", text) and "withdraw" not in text:
        add(
            OntologyClause(
                predicate="produces",
                arg_key="object",
                arg_value="card_in_hand",
                distance=0.20,
                origin="nl",
            )
        )
    if re.search(r"\bramp\b", text):
        add(
            OntologyClause(
                predicate="produces",
                arg_key="object",
                arg_value="mana",
                distance=0.20,
                origin="nl",
            )
        )
    if re.search(r"\btokens?\b", text) or re.search(r"\bfichas?\b", text):
        add(
            OntologyClause(
                predicate="produces",
                arg_key="object",
                arg_value="token",
                distance=0.12,
                origin="nl",
            )
        )
        add(
            OntologyClause(
                predicate="emits",
                arg_key="event",
                arg_value="token_created",
                distance=0.12,
                origin="nl",
            )
        )

    families = requirement_families(text)
    if skip_extra_combat:
        families = [name for name in families if name != "extra_combat"]
    for clause in _clauses_from_families(text, families):
        add(clause)

    return clauses


def _identity_ok(color_identity: str | None, allowed_colors: list[str] | None) -> bool:
    if allowed_colors is None:
        return True
    allowed = set(allowed_colors)
    try:
        colors = set(json.loads(color_identity or "[]"))
    except json.JSONDecodeError:
        colors = set()
    return colors.issubset(allowed)


def _clause_matches_row(
    clause: OntologyClause,
    predicate: str,
    arg_key: str | None,
    arg_value: str | None,
) -> bool:
    if clause.predicate != predicate:
        return False
    if clause.arg_value and clause.arg_value != (arg_value or ""):
        return False
    if clause.arg_key and clause.arg_key != (arg_key or ""):
        return False
    return True


def _unique_clauses(clauses: list[OntologyClause]) -> list[OntologyClause]:
    by_key: dict[tuple[str, str | None, str | None], OntologyClause] = {}
    order: list[tuple[str, str | None, str | None]] = []
    for clause in clauses:
        key = (clause.predicate, clause.arg_key, clause.arg_value)
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = clause
            order.append(key)
        elif clause.distance < prev.distance:
            by_key[key] = clause
    return [by_key[key] for key in order]


def search_ontology_clauses(
    conn: sqlite3.Connection,
    clauses: list[OntologyClause],
    allowed_colors: list[str] | None = None,
    k: int = 200,
) -> list[dict]:
    """Return hybrid_search-shaped hits for compiled ontology clauses."""
    if not clauses:
        return []

    unique = _unique_clauses(clauses)
    where_parts: list[str] = []
    params: list[Any] = []
    for clause in unique:
        if clause.arg_key and clause.arg_value:
            where_parts.append("(p.predicate = ? AND p.arg_key = ? AND p.arg_value = ?)")
            params.extend([clause.predicate, clause.arg_key, clause.arg_value])
        elif clause.arg_value:
            where_parts.append("(p.predicate = ? AND p.arg_value = ?)")
            params.extend([clause.predicate, clause.arg_value])
        elif clause.arg_key:
            where_parts.append("(p.predicate = ? AND p.arg_key = ?)")
            params.extend([clause.predicate, clause.arg_key])
        else:
            where_parts.append("(p.predicate = ?)")
            params.append(clause.predicate)

    # Two joins instead of `ON c.id = p.card_id OR (... c.name = p.card_name)`:
    # with the OR, SQLite cannot use the primary key and scans every card for
    # every matching predicate row (minutes per query on the full catalog).
    # Rows without a card_id are rare and keep the by-name match.
    where = " OR ".join(where_parts)
    columns = """p.card_id, p.card_name, p.predicate, p.arg_key, p.arg_value,
               c.name, c.type_line, c.oracle_text, c.color_identity,
               c.cmc, c.legalities"""
    sql = f"""
        SELECT {columns}
          FROM ontology_predicates p
     LEFT JOIN cards c ON c.id = p.card_id
         WHERE p.card_id IS NOT NULL AND ({where})
     UNION ALL
        SELECT {columns}
          FROM ontology_predicates p
     LEFT JOIN cards c ON c.name = p.card_name COLLATE NOCASE
         WHERE p.card_id IS NULL AND ({where})
    """
    params = params + params

    by_key: dict[str, dict[str, Any]] = {}
    try:
        for row in conn.execute(sql, params):
            (
                card_id,
                card_name,
                predicate,
                arg_key,
                arg_value,
                name,
                type_line,
                oracle_text,
                color_identity,
                cmc,
                legalities,
            ) = row
            display = name or card_name
            key = str(card_id or display or "").strip()
            if not key or not display:
                continue
            for clause in unique:
                if not _clause_matches_row(clause, predicate, arg_key, arg_value):
                    continue
                current = by_key.get(key)
                if current is None:
                    by_key[key] = {
                        "name": display,
                        "type_line": type_line or "",
                        "oracle_text": oracle_text or "",
                        "color_identity": color_identity or "[]",
                        "cmc": cmc,
                        "legalities": legalities,
                        "has_card": name is not None,
                        "distance": clause.distance,
                    }
                else:
                    current["distance"] = min(current["distance"], clause.distance) - 0.02
    except sqlite3.OperationalError:
        return []

    hits: list[dict] = []
    for item in by_key.values():
        if allowed_colors is not None:
            if not item["has_card"]:
                continue
            if not _identity_ok(item["color_identity"], allowed_colors):
                continue
        if item["has_card"] and not is_searchable_card(
            item["name"], item["type_line"], item["legalities"]
        ):
            continue
        hits.append(
            {
                "name": item["name"],
                "type_line": item["type_line"],
                "oracle_text": item["oracle_text"],
                "color_identity": item["color_identity"],
                "text": card_document(item["type_line"], item["oracle_text"]),
                "distance": max(0.001, float(item["distance"])),
                "cmc": item["cmc"],
                "source": "ontology",
            }
        )

    hits.sort(key=lambda hit: (hit["distance"], hit["name"]))
    limit = max(int(k), 0)
    return hits[:limit] if limit else hits


def search_ontology(
    conn: sqlite3.Connection,
    query: str,
    allowed_colors: list[str] | None = None,
    k: int = 200,
) -> list[dict]:
    """Compile the query, then search the predicate index."""
    return search_ontology_clauses(
        conn,
        compile_search_intent(query).clauses,
        allowed_colors=allowed_colors,
        k=k,
    )
