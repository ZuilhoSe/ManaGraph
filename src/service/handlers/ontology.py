"""Database-backed review endpoints for the Forge/Scryfall ontology join."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from catalog import DB_NAME, ensure_schema
from ontology.model_config import (
    CARD_FIELDS,
    default_model_config,
    model_field_metadata,
    normalize_model_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GOLD_SET_PATH = PROJECT_ROOT / "data" / "ontology" / "gold_set_v1.jsonl"
MODEL_CONFIG_PATH = PROJECT_ROOT / "data" / "ontology" / "model_config_v1.json"
_SCRIPTS = PROJECT_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _connect(db_path: str = DB_NAME) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def get_model_config() -> dict[str, Any]:
    if MODEL_CONFIG_PATH.is_file():
        try:
            config = json.loads(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = default_model_config()
    else:
        config = default_model_config()
    return {"config": normalize_model_config(config), "fields": model_field_metadata()}


def save_model_config(value: dict[str, Any], db_path: str = DB_NAME) -> dict[str, Any]:
    config = normalize_model_config(value)
    MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            ("ontology_model_config", str(MODEL_CONFIG_PATH)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"config": config, "fields": model_field_metadata(), "path": str(MODEL_CONFIG_PATH)}


def _add_catalog_value(values: dict[str, Counter[str]], field: str, value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _add_catalog_value(values, field, item)
        return
    if isinstance(value, dict):
        for key in value:
            _add_catalog_value(values, f"{field}.{key}", value[key])
        return
    text = "<empty>" if value is None or value == "" else str(value)
    values.setdefault(field, Counter())
    values[field][text] += 1


def _model_catalog_values(db_path: str = DB_NAME) -> dict[str, Counter[str]]:
    values: dict[str, Counter[str]] = {}
    conn = _connect(db_path)
    try:
        cards = conn.execute("SELECT scryfall_json FROM cards").fetchall()
        for row in cards:
            raw = _loads(row["scryfall_json"], {})
            if not isinstance(raw, dict) or not raw:
                continue
            semantic = dict(raw)
            semantic["mana_value"] = raw.get("mana_value", raw.get("cmc"))
            for field, _ in CARD_FIELDS:
                _add_catalog_value(values, f"semantic.{field}", semantic.get(field))
            for field, value in raw.items():
                _add_catalog_value(values, f"raw.{field}", value)
        forge_rows = conn.execute("SELECT record_json FROM forge_records").fetchall()
        for row in forge_rows:
            raw = _loads(row["record_json"], {})
            if not isinstance(raw, dict):
                continue
            for field, value in raw.items():
                _add_catalog_value(values, f"raw.{field}", value)
            card = raw.get("card") or {}
            for field, value in (card.get("metadata") or {}).items():
                _add_catalog_value(values, f"card.metadata.{field}", value)
            for section in ("effects", "triggers", "costs"):
                for item in raw.get(section) or []:
                    if isinstance(item, dict):
                        _add_catalog_value(values, f"{section}.kind", item.get("kind"))
                        _add_catalog_value(values, f"{section}.ability", item.get("ability"))
                        for key, value in (item.get("params") or {}).items():
                            _add_catalog_value(values, f"{section}.params.{key}", value)
        return values
    finally:
        conn.close()


def model_field_catalog(db_path: str = DB_NAME) -> list[dict[str, Any]]:
    values = _model_catalog_values(db_path)
    fields = []
    for field, counter in sorted(values.items()):
        all_values = sorted(counter)
        fields.append(
            {
                "id": field,
                "label": field,
                "value_count": len(all_values),
                "observations": sum(counter.values()),
                "sample_values": all_values[:50],
                "truncated": len(all_values) > 50,
            }
        )
    return fields


def model_field_values(
    source: str,
    field: str,
    query: str = "",
    page: int = 1,
    page_size: int = 100,
    db_path: str = DB_NAME,
) -> dict[str, Any]:
    prefix = "semantic." if source == "scryfall" else "raw."
    if source not in {"scryfall", "forge"}:
        raise ValueError("source must be scryfall or forge")
    if not field.startswith(prefix) and not (
        source == "forge" and (field.startswith("card.") or field.startswith("effects.") or field.startswith("triggers."))
    ):
        raise ValueError(f"field does not belong to {source}: {field}")
    counter = _model_catalog_values(db_path).get(field, Counter())
    items = sorted(counter)
    if query.strip():
        needle = query.strip().casefold()
        items = [item for item in items if needle in item.casefold()]
    page = max(page, 1)
    page_size = min(max(page_size, 1), 500)
    start = (page - 1) * page_size
    return {
        "source": source,
        "field": field,
        "values": [
            {"value": item, "count": counter[item]}
            for item in items[start:start + page_size]
        ],
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "pages": (len(items) + page_size - 1) // page_size,
    }


def rebuild_model_facts(db_path: str = DB_NAME) -> dict[str, Any]:
    """Refresh Final from stored layers and rematch unmatched DFC/split cards."""
    from enrich_ontology import rebuild_ontology_model

    return rebuild_ontology_model(
        db_path,
        config=get_model_config()["config"],
        rematch=True,
    )


def _scryfall(row: sqlite3.Row) -> dict[str, Any]:
    raw = _loads(row["scryfall_json"], {})
    if isinstance(raw, dict) and raw:
        return raw
    return {
        "id": row["id"],
        "name": row["name"],
        "mana_cost": row["mana_cost"] or "",
        "cmc": row["cmc"],
        "oracle_text": row["oracle_text"] or "",
        "color_identity": _loads(row["color_identity"], []),
        "type_line": row["type_line"] or "",
        "legalities": _loads(row["legalities"], {}),
        "keywords": _loads(row["keywords"], []),
        "prices": {"usd": row["price_usd"], "eur": row["price_eur"]},
    }


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "mana_cost": row["mana_cost"] or "",
        "cmc": row["cmc"],
        "type_line": row["type_line"] or "",
        "color_identity": _loads(row["color_identity"], []),
        "keywords": _loads(row["keywords"], []),
        "forge_match_status": row["forge_match_status"] or "unmatched",
        "review_status": row["review_status"] or "unreviewed",
        "forge_facts": _loads(row["forge_facts_json"], {}),
    }


def _detail(row: sqlite3.Row) -> dict[str, Any]:
    result = _summary(row)
    result.update(
        {
            "scryfall": _scryfall(row),
            "canonical_facts": _loads(row["canonical_facts_json"], {}),
            "resolved_facts": _loads(row["resolved_facts_json"], {}),
            "model_facts": _loads(row["model_facts_json"], {}),
            "forge": _loads(row["forge_json"], None),
            "forge_candidates": _loads(row["forge_candidates_json"], []),
            "forge_warnings": _loads(row["forge_warnings_json"], []),
            "review": {
                "status": row["review_status"] or "unreviewed",
                "selected_source": row["selected_source"] or "resolved",
                "field_checks": _loads(row["field_checks_json"], {}),
                "labels": _loads(row["review_labels_json"], []),
                "notes": row["review_notes"] or "",
                "reviewed_at": row["reviewed_at"],
            },
        }
    )
    return result


def ontology_stats(db_path: str = DB_NAME) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        matched_cards = conn.execute(
            "SELECT COUNT(*) FROM ontology_cards WHERE forge_match_status='matched'"
        ).fetchone()[0]
        forge_counts = {
            row["match_status"]: row["count"]
            for row in conn.execute(
                "SELECT match_status, COUNT(*) AS count FROM forge_records GROUP BY match_status"
            )
        }
        forge_total = sum(forge_counts.values())
        statuses = {
            row["status"]: row["count"]
            for row in conn.execute(
                """
                SELECT COALESCE(r.status, 'unreviewed') AS status, COUNT(*) AS count
                FROM cards c
                LEFT JOIN ontology_reviews r ON r.card_id = c.id
                GROUP BY COALESCE(r.status, 'unreviewed')
                """
            )
        }
        return {
            "cards": total,
            "forge_records": forge_total,
            "forge_matched": forge_counts.get("matched", 0),
            "forge_unmatched": forge_counts.get("unmatched", 0),
            "cards_with_forge": matched_cards,
            "reviews": statuses,
        }
    finally:
        conn.close()


def list_ontology_cards(
    *,
    query: str = "",
    card_type: str = "",
    color: str = "",
    keyword: str = "",
    forge_status: str = "",
    review_status: str = "",
    page: int = 1,
    page_size: int = 50,
    db_path: str = DB_NAME,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    clauses: list[str] = []
    params: list[Any] = []
    if query.strip():
        term = f"%{query.strip().lower()}%"
        clauses.append(
            "(LOWER(c.name) LIKE ? OR LOWER(c.oracle_text) LIKE ? OR LOWER(c.type_line) LIKE ?)"
        )
        params.extend([term, term, term])
    if card_type.strip():
        clauses.append("LOWER(c.type_line) LIKE ?")
        params.append(f"%{card_type.strip().lower()}%")
    if color.strip().upper() == "C":
        clauses.append("(c.color_identity IS NULL OR c.color_identity IN ('[]', ''))")
    elif color.strip():
        clauses.append("c.color_identity LIKE ?")
        params.append(f'%"{color.strip().upper()}"%')
    if keyword.strip():
        clauses.append("LOWER(COALESCE(c.keywords, '[]')) LIKE ?")
        params.append(f"%{keyword.strip().lower()}%")
    if forge_status.strip():
        clauses.append("COALESCE(o.forge_match_status, 'unmatched') = ?")
        params.append(forge_status.strip())
    if review_status.strip():
        clauses.append("COALESCE(r.status, 'unreviewed') = ?")
        params.append(review_status.strip())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _connect(db_path)
    try:
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM cards c
            LEFT JOIN ontology_cards o ON o.card_id = c.id
            LEFT JOIN ontology_reviews r ON r.card_id = c.id
            {where}
            """,
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT c.*, COALESCE(o.forge_match_status, 'unmatched') AS forge_match_status,
                   o.canonical_facts_json, o.forge_facts_json,
                   COALESCE(r.status, 'unreviewed') AS review_status
            FROM cards c
            LEFT JOIN ontology_cards o ON o.card_id = c.id
            LEFT JOIN ontology_reviews r ON r.card_id = c.id
            {where}
            ORDER BY c.name COLLATE NOCASE
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "cards": [_summary(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }
    finally:
        conn.close()


def get_ontology_card(card_id: str, db_path: str = DB_NAME) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT c.*, COALESCE(o.forge_match_status, 'unmatched') AS forge_match_status,
                   o.canonical_facts_json, o.resolved_facts_json, o.model_facts_json, o.forge_json,
                   o.forge_facts_json, o.forge_candidates_json,
                   o.forge_warnings_json, COALESCE(r.status, 'unreviewed') AS review_status,
                   r.selected_source, r.field_checks_json,
                   r.labels_json AS review_labels_json, r.notes AS review_notes,
                   r.reviewed_at
            FROM cards c
            LEFT JOIN ontology_cards o ON o.card_id = c.id
            LEFT JOIN ontology_reviews r ON r.card_id = c.id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        return _detail(row) if row else None
    finally:
        conn.close()


def save_ontology_review(
    card_id: str,
    status: str,
    labels: list[str] | None = None,
    notes: str = "",
    selected_source: str = "resolved",
    field_checks: dict[str, Any] | None = None,
    db_path: str = DB_NAME,
) -> dict[str, Any]:
    allowed = {"unreviewed", "accepted", "rejected", "uncertain"}
    allowed_sources = {"scryfall", "forge", "resolved"}
    if status not in allowed:
        raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
    if selected_source not in allowed_sources:
        raise ValueError(f"selected_source must be one of: {', '.join(sorted(allowed_sources))}")
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone() is None:
            raise ValueError(f"unknown card_id: {card_id}")
        clean_labels = sorted({str(label).strip() for label in (labels or []) if str(label).strip()})
        reviewed_at = None if status == "unreviewed" else _now()
        conn.execute(
            """
            INSERT INTO ontology_reviews
              (card_id, status, selected_source, field_checks_json, labels_json, notes, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(card_id) DO UPDATE SET
              status=excluded.status, labels_json=excluded.labels_json,
              notes=excluded.notes, reviewed_at=excluded.reviewed_at,
              selected_source=excluded.selected_source,
              field_checks_json=excluded.field_checks_json
            """,
            (
                card_id,
                status,
                selected_source,
                json.dumps(field_checks or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(clean_labels, ensure_ascii=False),
                notes.strip(),
                reviewed_at,
            ),
        )
        conn.commit()
        return get_ontology_card(card_id, db_path) or {}
    finally:
        conn.close()


def export_gold_set(db_path: str = DB_NAME, output_path: str | Path = GOLD_SET_PATH) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT c.*, o.canonical_facts_json, o.resolved_facts_json, o.model_facts_json,
                   o.forge_facts_json, o.forge_candidates_json,
                   COALESCE(r.status, 'unreviewed') AS review_status,
                   r.selected_source, r.field_checks_json,
                   r.labels_json AS review_labels_json, r.notes AS review_notes,
                   r.reviewed_at
            FROM cards c
            LEFT JOIN ontology_cards o ON o.card_id = c.id
            JOIN ontology_reviews r ON r.card_id = c.id
            WHERE r.status IN ('accepted', 'rejected', 'uncertain')
            ORDER BY c.name COLLATE NOCASE
            """
        ).fetchall()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            for row in rows:
                record = {
                    "card_id": row["id"],
                    "name": row["name"],
                    "status": row["review_status"],
                    "selected_source": row["selected_source"] or "resolved",
                    "field_checks": _loads(row["field_checks_json"], {}),
                    "labels": _loads(row["review_labels_json"], []),
                    "notes": row["review_notes"] or "",
                    "reviewed_at": row["reviewed_at"],
                    "scryfall": _scryfall(row),
                    "canonical_facts": _loads(row["canonical_facts_json"], {}),
                    "resolved_facts": _loads(row["resolved_facts_json"], {}),
                    "model_facts": _loads(row["model_facts_json"], {}),
                    "forge_facts": _loads(row["forge_facts_json"], {}),
                    "forge_candidates": _loads(row["forge_candidates_json"], []),
                }
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return {"count": len(rows), "path": str(output), "download": "/api/ontology/gold-set/download"}
    finally:
        conn.close()
