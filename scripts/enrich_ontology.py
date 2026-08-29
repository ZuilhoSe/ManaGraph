#!/usr/bin/env python3
"""Join the pinned Forge artifact to the canonical Scryfall catalog.

The SQLite ``cards`` table remains the backwards-compatible catalog.  This
script adds an additive ontology view beside it, retaining both complete raw
records and the normalized Forge facts used by the review UI.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from catalog import DB_NAME, ensure_schema  # noqa: E402
from data_collection import normalize_card_name  # noqa: E402
from mine_forge import _face_facts  # noqa: E402
from ontology.model_config import (  # noqa: E402
    build_model_facts,
    canonical_facts_from_scryfall,
    default_model_config,
    normalize_model_config,
    parse_mana_cost,
    parse_type_line,
)

DEFAULT_MODEL_CONFIG_PATH = PROJECT_ROOT / "data" / "ontology" / "model_config_v1.json"

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(name: str | None) -> str:
    return normalize_card_name(name).casefold()


def _json(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _load_forge(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid Forge JSONL at line {line_number}: {exc}") from exc
            if not isinstance(row, dict) or not isinstance(row.get("card"), dict):
                raise ValueError(f"Forge row {line_number} has no card object")
            _ensure_facts(row)
            rows.append(row)
    return rows


def _load_model_config(path: str | Path | None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_MODEL_CONFIG_PATH
    if not config_path.is_file():
        return default_model_config()
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid model configuration {config_path}: {exc}") from exc
    return normalize_model_config(value)


def _ensure_facts(row: dict[str, Any]) -> None:
    """Backfill facts for artifacts mined before the facts layer existed."""
    card = row["card"]
    faces = card.get("faces") or []
    for face in faces:
        if "facts" not in face:
            face["facts"] = _face_facts(
                face.get("metadata") or {},
                face.get("effects") or [],
                face.get("triggers") or [],
            )
    if faces:
        card["facts"] = {
            "source": "forge",
            "front": faces[0].get("facts", {}),
            "faces": [face.get("facts", {}) for face in faces],
        }
    else:
        card.setdefault("facts", {"source": "forge", "front": {}, "faces": []})


def _fallback_scryfall(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "mana_cost": row["mana_cost"] or "",
        "cmc": row["cmc"],
        "oracle_text": row["oracle_text"] or "",
        "color_identity": _json(row["color_identity"], []),
        "type_line": row["type_line"] or "",
        "legalities": _json(row["legalities"], {}),
        "keywords": _json(row["keywords"], []),
        "prices": {
            "usd": row["price_usd"],
            "eur": row["price_eur"],
        },
    }


def _scryfall_json(row: sqlite3.Row) -> dict[str, Any]:
    data = _json(row["scryfall_json"], {})
    return data if isinstance(data, dict) and data else _fallback_scryfall(row)


SKIP_FORGE_LAYOUTS = frozenset(
    {
        "art_series",
        "planar",
        "scheme",
        "token",
        "emblem",
        "vanguard",
        "double_faced_token",
        "memorabilia",
    }
)


def _canonical_facts(row: sqlite3.Row) -> dict[str, Any]:
    """Select normalized factual fields while retaining the complete raw card."""
    return canonical_facts_from_scryfall(_scryfall_json(row), row)


def join_name_keys(name: str | None) -> list[str]:
    """Exact name plus `//` halves used to join Scryfall and Forge records."""
    keys: list[str] = []
    normalized = _key(name)
    if not normalized:
        return keys
    keys.append(normalized)
    if " // " in normalized:
        left, right = normalized.split(" // ", 1)
        if left:
            keys.append(left)
        if right:
            keys.append(right)
    return keys


def _collect_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            names.extend(_collect_names(item))
        return names
    text = str(value).strip()
    return [text] if text else []


def forge_join_keys(row: dict[str, Any]) -> list[str]:
    """Primary Forge name, face names, and AlternateMode split halves."""
    card = row.get("card") or {}
    names = _collect_names(card.get("name"))
    for face in card.get("faces") or []:
        names.extend(_collect_names(face.get("name")))
        metadata = face.get("metadata") or {}
        names.extend(_collect_names(metadata.get("Name")))
    keys: list[str] = []
    seen: set[str] = set()
    for name in names:
        for key in join_name_keys(name):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def scryfall_join_keys(name: str | None, canonical: dict[str, Any] | None = None) -> list[str]:
    names = _collect_names(name)
    for face in (canonical or {}).get("faces") or []:
        names.extend(_collect_names(face.get("name")))
    keys: list[str] = []
    seen: set[str] = set()
    for item in names:
        for key in join_name_keys(item):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _forge_display_name(forge: dict[str, Any] | None) -> str | None:
    if not forge:
        return None
    name = (forge.get("card") or {}).get("name")
    if isinstance(name, list):
        name = next((item for item in name if str(item).strip()), None)
    text = str(name or "").strip()
    return text or None


def _layout_skips_forge(canonical: dict[str, Any] | None) -> bool:
    layout = str((canonical or {}).get("layout") or "").strip().lower()
    return layout in SKIP_FORGE_LAYOUTS


def _index_forge_keys(forge_by_key: dict[str, dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    exact: dict[str, list[str]] = {}
    alias: dict[str, list[str]] = {}
    for forge_key, row in forge_by_key.items():
        keys = forge_join_keys(row)
        if not keys:
            continue
        exact.setdefault(keys[0], []).append(forge_key)
        for key in keys:
            alias.setdefault(key, []).append(forge_key)
    return exact, alias


def match_forge_key(
    name: str | None,
    canonical: dict[str, Any],
    exact_index: dict[str, list[str]],
    alias_index: dict[str, list[str]],
    used: set[str] | None = None,
    *,
    allow_alias: bool = True,
) -> str | None:
    """Return the unique unused Forge key for a Scryfall card, or None."""
    if _layout_skips_forge(canonical):
        return None
    used = used if used is not None else set()
    primary = _key(name)
    exact = [key for key in exact_index.get(primary, []) if key not in used]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1 or not allow_alias:
        return None
    found: list[str] = []
    seen: set[str] = set()
    for key in scryfall_join_keys(name, canonical):
        for forge_key in alias_index.get(key, []):
            if forge_key not in used and forge_key not in seen:
                seen.add(forge_key)
                found.append(forge_key)
    if len(found) == 1:
        return found[0]
    return None


def assign_forge_matches(
    cards: list[Any],
    canonical_by_id: dict[str, dict[str, Any]],
    forge_by_key: dict[str, dict[str, Any]],
    *,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map Scryfall card ids to Forge keys. Existing exact matches are kept."""
    exact_index, alias_index = _index_forge_keys(forge_by_key)
    assigned = dict(existing or {})
    used = set(assigned.values())
    for card in cards:
        card_id = card["id"]
        if card_id in assigned:
            continue
        matched = match_forge_key(
            card["name"],
            canonical_by_id[card_id],
            exact_index,
            alias_index,
            used,
            allow_alias=False,
        )
        if matched:
            assigned[card_id] = matched
            used.add(matched)
    for card in cards:
        card_id = card["id"]
        if card_id in assigned:
            continue
        matched = match_forge_key(
            card["name"],
            canonical_by_id[card_id],
            exact_index,
            alias_index,
            used,
            allow_alias=True,
        )
        if matched:
            assigned[card_id] = matched
            used.add(matched)
    return assigned


def _types_for_resolved(
    forge_row: dict[str, Any] | None,
    canonical_facts: dict[str, Any],
) -> dict[str, Any]:
    front = (((forge_row or {}).get("card") or {}).get("facts") or {}).get("front") or {}
    forge_types = front.get("types") if isinstance(front, dict) else None
    if isinstance(forge_types, dict) and (
        forge_types.get("card_types") or forge_types.get("supertypes") or forge_types.get("subtypes")
    ):
        return {
            "card_types": list(forge_types.get("card_types") or []),
            "supertypes": list(forge_types.get("supertypes") or []),
            "subtypes": list(forge_types.get("subtypes") or []),
        }
    return parse_type_line(str(canonical_facts.get("type_line") or ""))


def _mana_for_resolved(
    forge_row: dict[str, Any] | None,
    canonical_facts: dict[str, Any],
) -> dict[str, Any]:
    front = (((forge_row or {}).get("card") or {}).get("facts") or {}).get("front") or {}
    forge_mana = front.get("mana") if isinstance(front, dict) else None
    if isinstance(forge_mana, dict) and (
        forge_mana.get("symbols") or forge_mana.get("raw") or forge_mana.get("colored_pips")
    ):
        return {
            "symbols": list(forge_mana.get("symbols") or []),
            "colored_pips": dict(forge_mana.get("colored_pips") or {}),
            "generic_pips": forge_mana.get("generic_pips") or 0,
        }
    return parse_mana_cost(str(canonical_facts.get("mana_cost") or ""))


def _resolved_facts(
    forge_row: dict[str, Any] | None,
    canonical_facts: dict[str, Any],
) -> dict[str, Any]:
    """Create one non-duplicated, source-resolved semantic document."""
    forge_row = forge_row or {}
    forge_card = forge_row.get("card") or {}
    forge_facts = forge_card.get("facts") or {}
    mana = _mana_for_resolved(forge_row, canonical_facts)
    canonical = {
        key: value
        for key, value in canonical_facts.items()
        if key != "faces"
    }
    canonical["types"] = _types_for_resolved(forge_row, canonical_facts)
    canonical["symbols"] = mana["symbols"]
    canonical["colored_pips"] = mana["colored_pips"]
    canonical["generic_pips"] = mana["generic_pips"]
    forge_keywords = {
        keyword
        for face in forge_facts.get("faces") or []
        for keyword in face.get("keywords") or []
    }
    canonical_keywords = set(canonical.get("keywords") or [])
    mechanics = {
        "effects": forge_row.get("effects") or [],
        "costs": forge_row.get("costs") or [],
        "triggers": forge_row.get("triggers") or [],
        "subabilities": forge_row.get("subabilities") or [],
        "deck_has": forge_row.get("deck_has") or [],
        "deck_needs": forge_row.get("deck_needs") or [],
        "deck_hints": forge_row.get("deck_hints") or [],
        "forge_keywords_not_in_scryfall": sorted(forge_keywords - canonical_keywords),
        "ability_kinds": sorted(
            {
                str(record.get("kind") or "unknown")
                for record in (forge_row.get("effects") or []) + (forge_row.get("triggers") or [])
            }
        ),
    }
    mechanics["ability_counts"] = {
        kind: sum(1 for record in (forge_row.get("effects") or []) + (forge_row.get("triggers") or [])
                  if record.get("kind") == kind)
        for kind in mechanics["ability_kinds"]
    }
    mechanics["effect_count"] = len(mechanics["effects"])
    mechanics["trigger_count"] = len(mechanics["triggers"])
    return {
        "source": "scryfall+forge",
        "card": canonical,
        "faces": canonical_facts.get("faces") or [],
        "mechanics": mechanics,
    }


def enrich(
    forge_path: str | Path,
    *,
    db_path: str = DB_NAME,
    output_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    forge_rows = _load_forge(Path(forge_path))
    model_config = _load_model_config(config_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = _now()
    try:
        ensure_schema(conn)
        cards = conn.execute("SELECT * FROM cards ORDER BY name COLLATE NOCASE").fetchall()
        forge_by_key: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(forge_rows):
            name = str(row["card"].get("name") or "").strip()
            if not name:
                continue
            base_key = _key(name)
            forge_key = base_key
            if forge_key in forge_by_key:
                forge_key = f"{base_key}#{index}"
            forge_by_key[forge_key] = row

        canonical_by_id = {card["id"]: _canonical_facts(card) for card in cards}
        card_to_forge = assign_forge_matches(cards, canonical_by_id, forge_by_key)
        forge_to_card = {forge_key: card_id for card_id, forge_key in card_to_forge.items()}
        cards_by_id = {card["id"]: card for card in cards}

        conn.execute("DELETE FROM ontology_cards")
        conn.execute("DELETE FROM forge_records")
        matched = 0
        unmatched_forge = 0
        output_rows: list[dict[str, Any]] = []

        for forge_key, row in forge_by_key.items():
            name = _forge_display_name(row) or ""
            card = cards_by_id.get(forge_to_card.get(forge_key))
            status = "matched" if card else "unmatched"
            if card:
                matched += 1
            else:
                unmatched_forge += 1
            conn.execute(
                """
                INSERT INTO forge_records
                  (forge_record_key, forge_name, matched_card_id, match_status,
                   record_json, facts_json, candidates_json, warnings_json, enriched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    forge_key,
                    name,
                    card["id"] if card else None,
                    status,
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                    json.dumps(row["card"].get("facts") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(row.get("candidates") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(row.get("warnings") or [], ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )

        for card in cards:
            forge_key = card_to_forge.get(card["id"])
            forge = forge_by_key.get(forge_key) if forge_key else None
            status = "matched" if forge else "unmatched"
            forge_name = _forge_display_name(forge)
            facts = forge["card"].get("facts") if forge else {}
            candidates = forge.get("candidates") if forge else []
            warnings = forge.get("warnings") if forge else []
            canonical_facts = canonical_by_id[card["id"]]
            resolved_facts = _resolved_facts(forge, canonical_facts)
            model_facts = build_model_facts(
                canonical_facts, forge, resolved_facts, model_config
            )
            conn.execute(
                """
                INSERT INTO ontology_cards
                  (card_id, scryfall_name, forge_record_key, forge_name,
                   forge_match_status, forge_json, canonical_facts_json, resolved_facts_json,
                   model_facts_json, forge_facts_json,
                   forge_candidates_json, forge_warnings_json, enriched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card["id"],
                    card["name"],
                    forge_key,
                    forge_name,
                    status,
                    json.dumps(forge, ensure_ascii=False, sort_keys=True) if forge else None,
                    json.dumps(canonical_facts, ensure_ascii=False, sort_keys=True),
                    json.dumps(resolved_facts, ensure_ascii=False, sort_keys=True),
                    json.dumps(model_facts, ensure_ascii=False, sort_keys=True),
                    json.dumps(facts or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(candidates or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(warnings or [], ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            output_rows.append(
                {
                    "card_id": card["id"],
                    "scryfall": _scryfall_json(card),
                    "forge": forge,
                    "model": model_facts,
                    "forge_match_status": status,
                }
            )

        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_enriched_at", now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_scryfall_cards", str(len(cards))),
        )
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_forge_records", str(len(forge_rows))),
        )
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_forge_matches", str(matched)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_model_config", str(config_path or DEFAULT_MODEL_CONFIG_PATH)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            for row in output_rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    return {
        "scryfall_cards": len(cards),
        "forge_records": len(forge_rows),
        "matched": matched,
        "unmatched_forge": unmatched_forge,
        "output_path": str(output_path) if output_path else None,
        "config_path": str(config_path or DEFAULT_MODEL_CONFIG_PATH),
        "db_path": db_path,
    }


def rematch_forge_joins(db_path: str = DB_NAME) -> dict[str, Any]:
    """Pair unmatched DFC/split/adventure cards to unused Forge records.

    Existing exact matches are kept. art_series / plane / scheme stay unmatched.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = _now()
    newly_matched = 0
    try:
        ensure_schema(conn)
        cards = conn.execute("SELECT * FROM cards ORDER BY name COLLATE NOCASE").fetchall()
        forge_by_key: dict[str, dict[str, Any]] = {}
        for row in conn.execute("SELECT forge_record_key, record_json FROM forge_records"):
            data = _json(row["record_json"], {})
            if isinstance(data, dict) and isinstance(data.get("card"), dict):
                _ensure_facts(data)
                forge_by_key[row["forge_record_key"]] = data
        if not forge_by_key:
            return {"newly_matched": 0, "cards": len(cards), "forge_records": 0}

        ontology_rows = {
            row["card_id"]: row
            for row in conn.execute("SELECT * FROM ontology_cards")
        }
        existing = {
            card_id: row["forge_record_key"]
            for card_id, row in ontology_rows.items()
            if row["forge_match_status"] == "matched" and row["forge_record_key"]
        }
        canonical_by_id = {card["id"]: _canonical_facts(card) for card in cards}

        assigned = assign_forge_matches(
            cards, canonical_by_id, forge_by_key, existing=existing
        )
        forge_to_card = {forge_key: card_id for card_id, forge_key in assigned.items()}
        cards_by_id = {card["id"]: card for card in cards}

        for card in cards:
            card_id = card["id"]
            forge_key = assigned.get(card_id)
            previous = ontology_rows.get(card_id)
            previous_key = previous["forge_record_key"] if previous else None
            if forge_key == previous_key and previous and previous["forge_match_status"] == (
                "matched" if forge_key else "unmatched"
            ):
                continue
            forge = forge_by_key.get(forge_key) if forge_key else None
            canonical_facts = canonical_by_id[card_id]
            resolved_facts = _resolved_facts(forge, canonical_facts)
            model_facts = build_model_facts(
                canonical_facts, forge, resolved_facts, _load_model_config(None)
            )
            status = "matched" if forge else "unmatched"
            if forge and previous_key != forge_key:
                newly_matched += 1
            conn.execute(
                """
                INSERT INTO ontology_cards
                  (card_id, scryfall_name, forge_record_key, forge_name,
                   forge_match_status, forge_json, canonical_facts_json, resolved_facts_json,
                   model_facts_json, forge_facts_json,
                   forge_candidates_json, forge_warnings_json, enriched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_id) DO UPDATE SET
                  forge_record_key=excluded.forge_record_key,
                  forge_name=excluded.forge_name,
                  forge_match_status=excluded.forge_match_status,
                  forge_json=excluded.forge_json,
                  canonical_facts_json=excluded.canonical_facts_json,
                  resolved_facts_json=excluded.resolved_facts_json,
                  model_facts_json=excluded.model_facts_json,
                  forge_facts_json=excluded.forge_facts_json,
                  forge_candidates_json=excluded.forge_candidates_json,
                  forge_warnings_json=excluded.forge_warnings_json,
                  enriched_at=excluded.enriched_at
                """,
                (
                    card_id,
                    card["name"],
                    forge_key,
                    _forge_display_name(forge),
                    status,
                    json.dumps(forge, ensure_ascii=False, sort_keys=True) if forge else None,
                    json.dumps(canonical_facts, ensure_ascii=False, sort_keys=True),
                    json.dumps(resolved_facts, ensure_ascii=False, sort_keys=True),
                    json.dumps(model_facts, ensure_ascii=False, sort_keys=True),
                    json.dumps((forge or {}).get("card", {}).get("facts") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps((forge or {}).get("candidates") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps((forge or {}).get("warnings") or [], ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )

        for forge_key, row in forge_by_key.items():
            card = cards_by_id.get(forge_to_card.get(forge_key))
            conn.execute(
                """
                UPDATE forge_records
                   SET matched_card_id=?, match_status=?
                 WHERE forge_record_key=?
                """,
                (
                    card["id"] if card else None,
                    "matched" if card else "unmatched",
                    forge_key,
                ),
            )

        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_forge_matches", str(len(assigned))),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "newly_matched": newly_matched,
        "matched": len(assigned) if forge_by_key else 0,
        "db_path": db_path,
    }


def rebuild_ontology_model(
    db_path: str = DB_NAME,
    *,
    config: dict[str, Any] | None = None,
    rematch: bool = True,
) -> dict[str, Any]:
    """Refresh canonical + resolved + Final from stored Scryfall/Forge rows."""
    rematch_result = rematch_forge_joins(db_path) if rematch else {"newly_matched": 0}
    model_config = normalize_model_config(config) if config else _load_model_config(None)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    count = 0
    try:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT o.card_id, o.forge_json, c.*
            FROM ontology_cards o
            JOIN cards c ON c.id = o.card_id
            """
        ).fetchall()
        for row in rows:
            forge = _json(row["forge_json"], None)
            if forge and not isinstance(forge.get("card"), dict):
                forge = None
            canonical = _canonical_facts(row)
            resolved = _resolved_facts(forge, canonical)
            model = build_model_facts(canonical, forge, resolved, model_config)
            conn.execute(
                """
                UPDATE ontology_cards
                   SET canonical_facts_json=?, resolved_facts_json=?, model_facts_json=?
                 WHERE card_id=?
                """,
                (
                    json.dumps(canonical, ensure_ascii=False, sort_keys=True),
                    json.dumps(resolved, ensure_ascii=False, sort_keys=True),
                    json.dumps(model, ensure_ascii=False, sort_keys=True),
                    row["card_id"],
                ),
            )
            count += 1
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_model_rebuilt_at", _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "cards_rebuilt": count,
        "newly_matched": rematch_result.get("newly_matched", 0),
        "config": model_config,
        "db_path": db_path,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Join Forge records to Scryfall cards.")
    parser.add_argument(
        "--forge-jsonl",
        default=str(PROJECT_ROOT / "data" / "ontology" / "forge_raw_v1.jsonl"),
    )
    parser.add_argument("--db", default=DB_NAME)
    parser.add_argument("--config", dest="config_path", default=str(DEFAULT_MODEL_CONFIG_PATH))
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "data" / "ontology" / "forge_scryfall_v1.jsonl"),
    )
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    result = enrich(
        args.forge_jsonl,
        db_path=args.db,
        output_path=args.output,
        config_path=args.config_path,
    )
    print(json.dumps(result, indent=2))
