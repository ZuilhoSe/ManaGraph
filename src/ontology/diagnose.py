"""Typed predicate deficits for diagnose_deck_json.

Queries ontology_predicates for the deck's card names. Deficits are mechanical
supply/demand mismatches (treasures vs outlets, tokens vs payoffs), never
named staples.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Iterable, Mapping

from catalog import DB_NAME
from ontology.search import predicates_for_names


def _qty(name: str, quantities: Mapping[str, int] | None) -> int:
    if not quantities:
        return 1
    if name in quantities:
        return max(int(quantities[name]), 1)
    lower = {str(key).lower(): int(value) for key, value in quantities.items()}
    return max(lower.get(name.lower(), 1), 1)


def summarize_predicates(
    rows: Iterable[tuple[str, str, str, str]],
    quantities: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Count distinct (card, signature) rows, weighted by deck quantity."""
    seen: set[tuple[str, str, str, str]] = set()
    counts: dict[str, int] = defaultdict(int)
    for card_name, predicate, arg_key, arg_value in rows:
        marker = (card_name.lower(), predicate, arg_key, arg_value)
        if marker in seen:
            continue
        seen.add(marker)
        weight = _qty(card_name, quantities)
        if predicate == "produces" and arg_key == "object":
            counts[f"produces:{arg_value}"] += weight
        elif predicate == "consumes" and arg_key == "object":
            counts[f"consumes:{arg_value}"] += weight
        elif predicate == "emits" and arg_key == "event":
            counts[f"emits:{arg_value}"] += weight
        elif predicate == "rewards" and arg_key == "event":
            counts[f"rewards:{arg_value}"] += weight
        elif predicate == "enables" and arg_key == "capability":
            counts[f"enables:{arg_value}"] += weight
        elif predicate == "recurs" and arg_key == "zone_from" and arg_value == "graveyard":
            counts["recurs:graveyard"] += weight
        elif predicate == "answers" and arg_key == "threat_class":
            counts[f"answers:{arg_value}"] += weight
        elif predicate == "protects" and arg_key == "target_class":
            counts[f"protects:{arg_value}"] += weight
        elif predicate == "tutors" and arg_key == "selector":
            counts[f"tutors:{arg_value}"] += weight
    return dict(counts)


def typed_deficits(counts: Mapping[str, int]) -> list[str]:
    """ONTOLOGY.md-style mismatches Stage 3.5 cannot express."""
    deficits: list[str] = []
    treasures = int(counts.get("produces:treasure") or 0)
    outlets = int(counts.get("enables:sac_outlet") or 0)
    if treasures > 0 and outlets == 0:
        deficits.append(f"{treasures} treasure sources, 0 sacrifice outlets")
    elif treasures >= 3 and outlets > 0 and treasures > outlets * 3:
        deficits.append(
            f"{treasures} treasure sources, {outlets} sacrifice outlets"
        )

    tokens = int(counts.get("produces:token") or 0) + int(
        counts.get("emits:token_created") or 0
    )
    token_payoffs = int(counts.get("rewards:token_created") or 0)
    if tokens > 0 and token_payoffs == 0:
        deficits.append(f"{tokens} token sources, 0 token-created payoffs")

    extra_combat = int(counts.get("enables:extra_combat") or 0)
    attack_payoffs = int(counts.get("rewards:attack") or 0)
    if attack_payoffs >= 3 and extra_combat == 0:
        deficits.append(f"{attack_payoffs} attack payoffs, 0 extra combat")

    reanimates = int(counts.get("recurs:graveyard") or 0)
    gy_fuel = int(counts.get("produces:creature_in_graveyard") or 0) + int(
        counts.get("produces:card_in_graveyard") or 0
    )
    if reanimates > 0 and gy_fuel == 0:
        deficits.append(
            f"{reanimates} reanimation effects, 0 mill/self-fill sources"
        )
    elif reanimates > 0 and gy_fuel > 0 and reanimates > gy_fuel * 2:
        deficits.append(
            f"{reanimates} reanimation effects, {gy_fuel} graveyard fill sources"
        )

    draws = int(counts.get("emits:draw") or 0) + int(
        counts.get("produces:card_in_hand") or 0
    )
    draw_payoffs = int(counts.get("rewards:draw") or 0)
    if draw_payoffs > 0 and draws == 0:
        deficits.append(f"{draw_payoffs} draw payoffs, 0 draw sources")

    landfall_payoffs = int(counts.get("rewards:landfall") or 0)
    land_events = int(counts.get("emits:landfall") or 0) + int(
        counts.get("produces:land_in_play") or 0
    )
    if landfall_payoffs > 0 and land_events == 0:
        deficits.append(f"{landfall_payoffs} landfall payoffs, 0 extra land / land ETB")

    return deficits


def interaction_snapshot(
    names: Iterable[str],
    db_path: str = DB_NAME,
) -> dict[str, object]:
    """answers/protects counts. Informational — not a legality constraint."""
    conn = sqlite3.connect(db_path)
    try:
        rows = predicates_for_names(conn, names)
    finally:
        conn.close()
    counts = summarize_predicates(rows)
    return {
        "answers": {
            key.split(":", 1)[1]: value
            for key, value in counts.items()
            if key.startswith("answers:")
        },
        "protects": {
            key.split(":", 1)[1]: value
            for key, value in counts.items()
            if key.startswith("protects:")
        },
        "queried": True,
    }


def attach_ontology_deficits(
    report: dict,
    names: Iterable[str],
    db_path: str = DB_NAME,
    quantities: Mapping[str, int] | None = None,
) -> dict:
    """Mutate a diagnose report with predicate counts and typed deficits."""
    conn = sqlite3.connect(db_path)
    try:
        rows = predicates_for_names(conn, names)
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    counts = summarize_predicates(rows, quantities)
    extra = typed_deficits(counts)
    report["ontology_counts"] = counts
    report["ontology_deficits"] = extra
    existing = list(report.get("deficits") or [])
    report["deficits"] = existing + extra
    return report
