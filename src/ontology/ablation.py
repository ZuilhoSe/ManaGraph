"""ONTOLOGY.md Phase E: switchable retrieval / score / cut rows and eval metrics.

Row mapping vs the paper table:

- A MiniLM concat retrieve, Stage 3.5 score, concat geometry, kNN-density cut
- B MiniLM concat retrieve, Stage 3.5 score, multi-view geometry, kNN cut
- C hybrid retrieve, + ontology pairs, multi-view geometry, kNN cut
- D hybrid retrieve, + ontology pairs, multi-view geometry, signature cut
- E ontology-filter retrieve, ontology pairs + predicate geometry, signature cut

Default product row is D (current solver). B's "multi-view retrieval" in the
table is score-side: there is no separate multi-view retrieval index.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

from ontology.graph import (
    SELF_PAIRED_BUCKETS,
    empty_pred_sets,
    predicate_signature,
)

ANSWER_CLASSES = (
    "creature",
    "artifact",
    "enchantment",
    "board",
    "stack",
    "graveyard",
)
ROWS = ("A", "B", "C", "D", "E")
DEFAULT_ROW = "D"


@dataclass(frozen=True)
class AblationRow:
    row: str
    retrieval: str
    score: str
    geometry: str
    cut: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


_ROWS: dict[str, AblationRow] = {
    "A": AblationRow("A", "minilm", "symbolic35", "concat", "knn"),
    "B": AblationRow("B", "minilm", "symbolic35", "multiview", "knn"),
    "C": AblationRow("C", "hybrid", "ontology_pairs", "multiview", "knn"),
    "D": AblationRow("D", "hybrid", "ontology_pairs", "multiview", "signature"),
    "E": AblationRow("E", "ontology", "ontology_pairs", "predicates", "signature"),
}


def resolve_ablation(name: str | None = None) -> AblationRow:
    raw = (name or os.environ.get("MANAGRAPH_ABLATION") or DEFAULT_ROW).strip().upper()
    return _ROWS.get(raw) or _ROWS[DEFAULT_ROW]


def _truthy(value: str | None) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "":
        return None
    if text in {"0", "false", "off", "no"}:
        return False
    if text in {"1", "true", "on", "yes"}:
        return True
    return None


def ontology_score_enabled(row: AblationRow, deck=None) -> bool:
    flag = _truthy(os.environ.get("MANAGRAPH_ONTOLOGY_SCORE"))
    if flag is False:
        return False
    if getattr(deck, "ontology_score", None) is False:
        return False
    if flag is True:
        return True
    return row.score == "ontology_pairs"


def geometry_mode(row: AblationRow) -> str:
    flag = _truthy(os.environ.get("MANAGRAPH_ONTOLOGY_GEOMETRY"))
    if flag is True:
        return "predicates"
    if flag is False and row.geometry == "predicates":
        return "multiview"
    return row.geometry


def has_live_pair(
    card_sets: Mapping[str, Iterable[str]] | None,
    deck_sets: Mapping[str, Iterable[str]] | None,
) -> bool:
    card = card_sets or {}
    deck = deck_sets or {}
    if any(card.get(bucket) for bucket in SELF_PAIRED_BUCKETS):
        return True
    return bool(
        (set(card.get("emits") or ()) & set(deck.get("rewards") or ()))
        or (set(card.get("rewards") or ()) & set(deck.get("emits") or ()))
        or (set(card.get("produces") or ()) & set(deck.get("consumes") or ()))
        or (set(card.get("consumes") or ()) & set(deck.get("produces") or ()))
    )


def _prefix_counts(counts: Mapping[str, int], prefix: str) -> dict[str, int]:
    head = prefix + ":"
    out: dict[str, int] = {}
    for key, value in (counts or {}).items():
        if key.startswith(head):
            name = key[len(head) :]
            if name:
                out[name] = int(value or 0)
    return out


def union_pred_sets(
    by_name: Mapping[str, Mapping[str, Iterable[str]]],
    skip: str | None = None,
) -> dict[str, set[str]]:
    union = empty_pred_sets()
    skip_key = (skip or "").lower()
    for name, sets in (by_name or {}).items():
        if skip_key and str(name).lower() == skip_key:
            continue
        for key in union:
            union[key] |= set(sets.get(key) or ())
    return union


def dead_card_names(
    by_name: Mapping[str, Mapping[str, Iterable[str]]],
) -> list[str]:
    """Cards with an ontology signature and no live emit/reward/produce/consume edge."""
    dead: list[str] = []
    for name, sets in (by_name or {}).items():
        if not predicate_signature(sets):
            continue
        others = union_pred_sets(by_name, skip=str(name))
        if not has_live_pair(sets, others):
            dead.append(str(name))
    return dead


def ontology_eval_metrics(
    diagnosis: Mapping | None,
    by_name: Mapping[str, Mapping[str, Iterable[str]]] | None = None,
) -> dict:
    """Matched-pair coverage, orphan/starved/dead rates, answer-class coverage."""
    report = diagnosis or {}
    flow = report.get("ontology_flow") or {}
    counts = report.get("ontology_counts") or {}
    rewards = _prefix_counts(counts, "rewards")
    emits = _prefix_counts(counts, "emits")
    consumes = {
        key: value
        for key, value in _prefix_counts(counts, "consumes").items()
        if key != "mana"
    }
    reward_events = [event for event, n in rewards.items() if n > 0]
    covered = [event for event in reward_events if int(emits.get(event) or 0) > 0]
    consume_objects = [obj for obj, n in consumes.items() if n > 0]
    starved = list(flow.get("starved") or [])
    orphans = list(flow.get("orphans") or [])
    answers = dict(flow.get("answers") or _prefix_counts(counts, "answers"))
    dead = dead_card_names(by_name or {})
    signed = [
        name
        for name, sets in (by_name or {}).items()
        if predicate_signature(sets)
    ]
    answer_hits = sum(1 for cls in ANSWER_CLASSES if int(answers.get(cls) or 0) > 0)
    return {
        "matched_pair_coverage": (
            len(covered) / len(reward_events) if reward_events else None
        ),
        "orphan_rate": (
            len(orphans) / len(reward_events) if reward_events else 0.0
        ),
        "starved_rate": (
            len(starved) / len(consume_objects) if consume_objects else 0.0
        ),
        "dead_card_rate": (len(dead) / len(signed) if signed else 0.0),
        "dead_cards": dead,
        "answer_class_coverage": answer_hits / len(ANSWER_CLASSES),
        "answer_classes": {
            cls: int(answers.get(cls) or 0) for cls in ANSWER_CLASSES
        },
        "orphan_events": [row.get("event") for row in orphans],
        "starved_objects": [row.get("object") for row in starved],
        "offplan_objects": [
            row.get("object") for row in (flow.get("offplan_producers") or [])
        ],
        "matched_events": list(flow.get("matched_events") or covered),
    }
