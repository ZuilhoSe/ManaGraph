"""LLM-free smoke test for Stage 1–2: catalog, fill/cut, validator.

Does not re-import SQLite inventory (that would stack copies). Reads the
text lists once into a DeckState.
"""

import json
import os
import re
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from catalog import DB_NAME, get_meta, get_oracle_card
from deck_state import DeckState
from rules_validator import CommanderValidator
from solver import DeckSolver


def _print_section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _catalog_stats():
    if not os.path.exists(DB_NAME):
        return None
    conn = sqlite3.connect(DB_NAME)
    try:
        n = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
        priced = 0
        if "price_usd" in cols:
            priced = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE price_usd IS NOT NULL"
            ).fetchone()[0]
        return {"cards": n, "has_price_usd": "price_usd" in cols, "priced": priced}
    finally:
        conn.close()


def _parse_list(path: str) -> dict[str, int]:
    pattern = re.compile(r"^(\d+)x?\s+(.+)$")
    cards = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            match = pattern.match(line)
            if not match:
                continue
            name = match.group(2).strip()
            cards[name] = cards.get(name, 0) + int(match.group(1))
    return cards


def _format_list(deck: DeckState) -> str:
    lines = []
    if deck.commander:
        lines.append(f"1 {deck.commander}")
    for name, qty in sorted(deck.card_list().items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"{qty} {name}")
    return "\n".join(lines) + ("\n" if lines else "")


def _save_list(deck: DeckState, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_format_list(deck))


def _summarize_validation(report: dict) -> dict:
    return {
        "valid": report.get("valid"),
        "complete": report.get("complete"),
        "slot_count": report.get("slot_count"),
        "land_count": report.get("land_count"),
        "curve": report.get("curve"),
        "budget_used": report.get("budget_used"),
        "warnings": report.get("warnings"),
        "color_errors": report.get("color_errors"),
        "singleton_errors": report.get("singleton_errors"),
        "format_errors": report.get("format_errors"),
        "size_errors": report.get("size_errors"),
        "price_errors": report.get("price_errors"),
        "commander_errors": report.get("commander_errors"),
    }


def main():
    retrieve = "--retrieve" in sys.argv
    _print_section("1. Catalog")
    stats = _catalog_stats()
    if not stats:
        print(f"No database at {DB_NAME}")
        print("Run: python src/scryfall_download.py")
        return 1
    print(json.dumps({**stats, "meta": get_meta()}, indent=2))
    krenko = get_oracle_card("Krenko, Mob Boss")
    print("Krenko:", "found" if krenko else "MISSING")
    if krenko:
        print(f"  type: {krenko['type_line']}")
        print(f"  identity: {krenko['color_identity']}")
        print(f"  usd: {krenko.get('price_usd')}")
    if not stats.get("has_price_usd"):
        print("NOTE: prices are missing. Re-run python src/scryfall_download.py for budget tests.")

    _print_section("2. Load test lists into DeckState (no SQLite stacking)")
    pool_path = os.path.join(BASE_DIR, "data", "test_pool.txt")
    deck_path = os.path.join(BASE_DIR, "data", "test_deck.txt")
    deck_cards = _parse_list(deck_path)
    pool_cards = _parse_list(pool_path)
    deck = DeckState(
        commander="Krenko, Mob Boss",
        cards=deck_cards,
        intent="improve",
        require_complete=True,
        candidate_pool=pool_cards,
    )
    if krenko:
        deck.identity = list(krenko["color_identity"])
        deck.set_commander("Krenko, Mob Boss")
    print("from test_deck.txt:", deck_cards)
    print("from test_pool.txt (candidate_pool):", pool_cards)
    print(json.dumps(deck.summary(), indent=2, default=str))

    _print_section("3. Validator (before solver)")
    validator = CommanderValidator()
    before = validator.validate_deck_state(deck)
    print(json.dumps(_summarize_validation(before), indent=2))

    _print_section(f"4. Solver fill/cut (retrieve={retrieve})")
    solver = DeckSolver()
    fill_report = solver.fill(deck, query="goblin tokens damage", retrieve=retrieve)
    cut_report = solver.cut(deck, query="goblin tokens damage")
    print("FILL added:", len(fill_report.get("added") or []))
    print("  first 15:", [x["name"] for x in (fill_report.get("added") or [])[:15]])
    print("CUT swapped:", cut_report.get("swapped"))
    print("CUT removed:", cut_report.get("removed"))
    print(
        f"slots={deck.slot_count()} remaining={deck.remaining_slots()} pool={deck.pool_count()}"
    )

    _print_section("5. Validator (after solver)")
    after = validator.validate_deck_state(deck)
    print(json.dumps(_summarize_validation(after), indent=2))
    print("\nCommitted list:")
    for name, qty in sorted(deck.card_list().items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {qty} {name}")

    out_path = os.path.join(BASE_DIR, "data", "solved_deck.txt")
    _save_list(deck, out_path)
    print(f"\nWrote {deck.slot_count()} cards to {out_path}")
    return 0 if after.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
