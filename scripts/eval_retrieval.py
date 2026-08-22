"""Offline, LLM-free benchmark for candidate_pool retrieval quality.

Retrieval queries come from src/archetypes.py::search_queries_for(archetype),
where archetype is inferred from the user's query text (infer_archetype()).
That gives some deck-shape awareness (e.g. a "counterspell" query pulls in
"counter target spell" for a control-classified deck), but the underlying
generic queries (still "draw a card" among others) carry the same short-query
vs. long-document embedding bias documented in eval/RETRIEVAL_FINDINGS.md --
archetype detection narrows the query set, it doesn't fix that bias.

No LLM involved: this runs the exact same DeckSolver._retrieve() path the
Solver uses in a real run (RAGSearcher + Chroma), against a fixed set of
curated archetypes. Same input -> same output, so it's safe to run before/after
a retrieval change and diff the numbers.

Usage:
    python scripts/eval_retrieval.py                  # all archetypes in eval/archetypes/
    python scripts/eval_retrieval.py aminatou_esper    # substring filter on archetype name
    python scripts/eval_retrieval.py --json            # machine-readable output
"""

from __future__ import annotations

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(BASE_DIR, "src")
ARCHETYPES_DIR = os.path.join(BASE_DIR, "eval", "archetypes")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from deck_state import DeckState
from solver import DeckSolver


def _load_archetypes(name_filter: str | None) -> list[dict]:
    archetypes = []
    for fname in sorted(os.listdir(ARCHETYPES_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(ARCHETYPES_DIR, fname)
        with open(path, encoding="utf-8") as handle:
            spec = json.load(handle)
        if name_filter and name_filter.lower() not in spec.get("name", fname).lower():
            continue
        archetypes.append(spec)
    return archetypes


def _run_one(solver: DeckSolver, spec: dict) -> dict:
    deck = DeckState(
        commander=spec["commander"],
        identity=list(spec.get("identity") or []),
        max_card_price=spec.get("max_card_price"),
        currency=spec.get("currency", "usd"),
    )
    retrieved = solver._retrieve(deck, spec.get("query", ""))
    pool = {name.lower() for name in retrieved}

    good = spec.get("good") or []
    bad = spec.get("bad") or []
    good_hits = [c for c in good if c.lower() in pool]
    bad_hits = [c for c in bad if c.lower() in pool]

    return {
        "name": spec.get("name"),
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


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv[1:]
    name_filter = args[0] if args else None

    specs = _load_archetypes(name_filter)
    if not specs:
        print(f"No archetype files matched in {ARCHETYPES_DIR} (filter={name_filter!r})")
        return 1

    solver = DeckSolver()
    results = [_run_one(solver, spec) for spec in specs]

    if as_json:
        print(json.dumps(results, indent=2))
        return 0

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
