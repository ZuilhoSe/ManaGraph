"""Universal deck build CLI: infer filters from the query, then Solver fill/cut.

Example:
  python scripts/build_deck.py --commander "Marina Vendrell" \\
    --query "rooms enchantress gate mana base and ramp" --retrieve
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from catalog import get_oracle_card
from deck_state import DeckState, infer_task
from mana import diagnose_deck, strategy_from_name
from rules_validator import CommanderValidator
from solver import DeckSolver


def _format_list(deck: DeckState) -> str:
    lines = []
    if deck.commander:
        lines.append(f"1 {deck.commander}")
    for name, qty in sorted(deck.card_list().items(), key=lambda kv: (-kv[1], kv[0].lower())):
        lines.append(f"{qty} {name}")
    return "\n".join(lines) + ("\n" if lines else "")


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", (name or "deck").lower()).strip("_") or "deck"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a Commander 99 via catalog filters + DeckSolver (no hardcoded seeds)."
    )
    parser.add_argument("--commander", required=True, help='e.g. "Marina Vendrell"')
    parser.add_argument(
        "--query",
        default="",
        help='Natural language plan, e.g. "rooms enchantress gate mana base and ramp"',
    )
    parser.add_argument(
        "--retrieve",
        action="store_true",
        help="Also seed candidate_pool from vector retrieval for glue cards.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional deck list path (default: data/deck_<commander>.txt)",
    )
    parser.add_argument(
        "--log",
        default="",
        help="Optional JSON log path (default: data/deck_<commander>_log.json)",
    )
    args = parser.parse_args(argv)

    commander = args.commander.strip()
    query = (args.query or "").strip() or f"build a full deck for {commander}"
    cmd = get_oracle_card(commander)
    if not cmd:
        print(f"Commander '{commander}' not in catalog. Run: python src/scryfall_download.py")
        return 1

    flags = infer_task(query, has_cards=False)
    flags["require_complete"] = True
    flags["intent"] = "build"

    deck = DeckState(
        commander=commander,
        identity=list(cmd["color_identity"]),
        **{k: flags[k] for k in (
            "intent",
            "owned_only",
            "require_complete",
            "archetype",
            "preferred_land_types",
            "theme_types",
            "land_types_strict",
        ) if k in flags},
    )
    deck.set_commander(commander)

    print("=" * 60)
    print("1. Inferred filters")
    print("=" * 60)
    print(f"commander: {deck.commander} {deck.identity}")
    print(f"archetype: {deck.archetype}")
    print(f"preferred_land_types: {deck.preferred_land_types}")
    print(f"theme_types: {deck.theme_types}")
    print(f"land_types_strict: {deck.land_types_strict}")
    print(f"query: {query}")

    print("\n" + "=" * 60)
    print(f"2. Solver solve(fill_to_99, retrieve={args.retrieve})")
    print("=" * 60)
    solver = DeckSolver()
    report = solver.solve(deck, query=query, fill_to_99=True)
    if args.retrieve and deck.remaining_slots() > 0:
        extra = solver.fill(deck, query=query, retrieve=True)
        print(f"extra retrieve fill added={len(extra.get('added') or [])}")
    cut = report.get("cut") or solver.cut(deck, query=query)
    fill = report.get("fill") or {}
    print(f"FILL added={len(fill.get('added') or [])}")
    print(f"CUT swapped={len((cut or {}).get('swapped') or [])}")
    print(f"slots={deck.slot_count()} remaining={deck.remaining_slots()} pool={deck.pool_count()}")

    print("\n" + "=" * 60)
    print("3. Validator + diagnosis")
    print("=" * 60)
    after = CommanderValidator().validate_deck_state(deck)
    print(json.dumps({
        "valid": after.get("valid"),
        "complete": after.get("complete"),
        "slot_count": after.get("slot_count"),
        "land_count": after.get("land_count"),
        "format_errors": after.get("format_errors"),
        "color_errors": after.get("color_errors"),
        "size_errors": after.get("size_errors"),
    }, indent=2))
    diagnosis = diagnose_deck(deck, strategy=strategy_from_name(deck.mana_strategy))

    # Composition summary for typed themes
    typed = {frag: 0 for frag in (deck.preferred_land_types + deck.theme_types)}
    for name, qty in deck.card_list().items():
        info = get_oracle_card(name) or {}
        tl = info.get("type_line") or ""
        for frag in list(typed):
            if frag.lower() in tl.lower():
                typed[frag] += qty
    print(f"composition: {typed} land_count={diagnosis.get('land_count')}")

    slug = _slug(commander)
    out_list = args.out or os.path.join(BASE_DIR, "data", f"deck_{slug}.txt")
    out_log = args.log or os.path.join(BASE_DIR, "data", f"deck_{slug}_log.json")
    os.makedirs(os.path.dirname(out_list) or ".", exist_ok=True)
    with open(out_list, "w", encoding="utf-8") as handle:
        handle.write(_format_list(deck))
    with open(out_log, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "query": query,
                "commander": commander,
                "filters": {
                    "archetype": deck.archetype,
                    "preferred_land_types": deck.preferred_land_types,
                    "theme_types": deck.theme_types,
                    "land_types_strict": deck.land_types_strict,
                },
                "retrieve": args.retrieve,
                "solve": report,
                "cut": cut,
                "composition": typed,
                "diagnosis": {
                    "land_count": diagnosis.get("land_count"),
                    "roles": diagnosis.get("roles"),
                    "deficits": diagnosis.get("deficits"),
                },
            },
            handle,
            indent=2,
            default=str,
        )
    print(f"\nWrote {deck.slot_count()} cards -> {out_list}")
    print(f"Wrote log -> {out_log}")
    return 0 if after.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
