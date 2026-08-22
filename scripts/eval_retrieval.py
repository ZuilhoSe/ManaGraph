"""Offline, LLM-free benchmark for candidate_pool retrieval quality.

Runs DeckSolver._retrieve() against:
  - eval/archetypes/*.json  (empty deck, curated good/bad)
  - eval/scenarios/*.json   (real deck export; labels from matching archetype
    when the scenario has no good/bad of its own)

Usage:
    python scripts/eval_retrieval.py
    python scripts/eval_retrieval.py aminatou_esper
    python scripts/eval_retrieval.py --scenarios
    python scripts/eval_retrieval.py --json
"""

from __future__ import annotations

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(BASE_DIR, "src")
ARCHETYPES_DIR = os.path.join(BASE_DIR, "eval", "archetypes")
SCENARIOS_DIR = os.path.join(BASE_DIR, "eval", "scenarios")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from deck_state import DeckState
from solver import DeckSolver


def _load_json_dir(directory: str, name_filter: str | None) -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    if not os.path.isdir(directory):
        return items
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(directory, fname)
        with open(path, encoding="utf-8") as handle:
            spec = json.load(handle)
        label = spec.get("name") or fname
        if name_filter and name_filter.lower() not in label.lower() and name_filter.lower() not in fname.lower():
            continue
        items.append((path, spec))
    return items


def _labels_for_scenario(path: str, scen: dict) -> tuple[list[str], list[str]]:
    """Prefer scenario-local good/bad; else borrow from a matching archetype file."""
    good = list(scen.get("good") or [])
    bad = list(scen.get("bad") or [])
    if good or bad:
        return good, bad
    base = os.path.basename(path).replace("_full_deck.json", ".json")
    # aminatou_esper_enchantments_full_deck -> aminatou_esper_enchantments.json
    arch_path = os.path.join(ARCHETYPES_DIR, base)
    if not os.path.isfile(arch_path):
        # fallback: commander-name fuzzy
        cmd = ((scen.get("deck") or {}).get("commander") or "").lower()
        for fname in os.listdir(ARCHETYPES_DIR) if os.path.isdir(ARCHETYPES_DIR) else []:
            if not fname.endswith(".json"):
                continue
            if cmd and cmd.split(",")[0].strip() in fname.lower():
                arch_path = os.path.join(ARCHETYPES_DIR, fname)
                break
    if os.path.isfile(arch_path):
        with open(arch_path, encoding="utf-8") as handle:
            arch = json.load(handle)
        return list(arch.get("good") or []), list(arch.get("bad") or [])
    return [], []


def _score(pool: set[str], good: list[str], bad: list[str], name: str) -> dict:
    good_hits = [c for c in good if c.lower() in pool]
    bad_hits = [c for c in bad if c.lower() in pool]
    return {
        "name": name,
        "pool_size": len(pool),
        "good_total": len(good),
        "good_found": len(good_hits),
        "good_recall": round(len(good_hits) / len(good), 3) if good else None,
        "good_missed": sorted(set(good) - set(good_hits)),
        "bad_total": len(bad),
        "bad_found": len(bad_hits),
        "bad_rate": round(len(bad_hits) / len(bad), 3) if bad else None,
        "bad_hit_names": sorted(bad_hits),
    }


def _run_archetype(solver: DeckSolver, spec: dict) -> dict:
    deck = DeckState(
        commander=spec["commander"],
        identity=list(spec.get("identity") or []),
        max_card_price=spec.get("max_card_price"),
        currency=spec.get("currency", "usd"),
    )
    retrieved = solver._retrieve(deck, spec.get("query", ""))
    pool = {name.lower() for name in retrieved}
    return _score(pool, spec.get("good") or [], spec.get("bad") or [], spec.get("name"))


def _run_scenario(solver: DeckSolver, path: str, scen: dict) -> dict:
    deck = DeckState.from_dict(scen.get("deck") or {})
    # Exports sometimes omit identity; resolve from commander for fair retrieval.
    if deck.commander and not deck.identity:
        from catalog import get_oracle_card

        info = get_oracle_card(deck.commander)
        if info:
            deck.identity = list(info.get("color_identity") or [])
    query = scen.get("query") or ""
    retrieved = solver._retrieve(deck, query)
    pool = {name.lower() for name in retrieved}
    good, bad = _labels_for_scenario(path, scen)
    name = f"scenario:{os.path.basename(path)}"
    return _score(pool, good, bad, name)


def _print_results(results: list[dict]) -> None:
    for r in results:
        print(f"\n=== {r['name']} ===")
        print(f"  candidate_pool size: {r['pool_size']}")
        print(f"  good recall: {r['good_found']}/{r['good_total']} ({r['good_recall']})")
        if r["good_missed"]:
            print(f"    missed: {', '.join(r['good_missed'])}")
        print(f"  bad hit rate: {r['bad_found']}/{r['bad_total']} ({r['bad_rate']})")
        if r["bad_hit_names"]:
            print(f"    hit: {', '.join(r['bad_hit_names'])}")

    avg_recall = [r["good_recall"] for r in results if r["good_recall"] is not None]
    avg_bad = [r["bad_rate"] for r in results if r["bad_rate"] is not None]
    if avg_recall or avg_bad:
        print("\n=== summary ===")
        if avg_recall:
            print(f"  avg good recall: {round(sum(avg_recall) / len(avg_recall), 3)}")
        if avg_bad:
            print(f"  avg bad rate:    {round(sum(avg_bad) / len(avg_bad), 3)}")


def main() -> int:
    argv = list(sys.argv[1:])
    as_json = "--json" in argv
    flags = {
        "--json",
        "--scenarios",
        "--all",
        "--archetypes-only",
        "--scenarios-only",
    }
    name_filter = next((a for a in argv if a not in flags), None)

    run_archetypes = "--scenarios-only" not in argv
    run_scenarios = "--archetypes-only" not in argv

    solver = DeckSolver()
    results: list[dict] = []

    if run_archetypes:
        for _path, spec in _load_json_dir(ARCHETYPES_DIR, name_filter):
            results.append(_run_archetype(solver, spec))

    if run_scenarios:
        for path, scen in _load_json_dir(SCENARIOS_DIR, name_filter):
            results.append(_run_scenario(solver, path, scen))

    if not results:
        print(
            f"No files matched "
            f"(archetypes={ARCHETYPES_DIR}, scenarios={SCENARIOS_DIR}, "
            f"filter={name_filter!r})"
        )
        return 1

    if as_json:
        print(json.dumps(results, indent=2))
        return 0

    _print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
