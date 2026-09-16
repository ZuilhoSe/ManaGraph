"""Derived ontology relations consumed by diagnose and the solver.

Layer 2 is computed, never annotated. Supply/demand mismatches become the same
repair records cut already scores, plus explicit `predicate:value` searches.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from deck_analysis.curve import cmc_bucket

_IGNORE_OBJECTS = frozenset({"mana"})
_SIGNATURE_SKIP = frozenset({("produces", "mana")})
_FLOW_CAP = 12
PRED_BUCKETS = (
    "emits",
    "rewards",
    "produces",
    "consumes",
    "enables",
    "protects",
    "answers",
    "tutors",
    "recurs",
)


def empty_pred_sets() -> dict[str, set[str]]:
    return {key: set() for key in PRED_BUCKETS}


def add_predicate_to_sets(
    bucket: dict[str, set[str]],
    predicate: str,
    arg_key: str,
    arg_value: str,
) -> None:
    """Map an index row onto the solver's signature buckets. Other keys are ignored."""
    if not arg_value:
        return
    if predicate == "emits" and arg_key == "event":
        bucket["emits"].add(arg_value)
    elif predicate == "rewards" and arg_key == "event":
        bucket["rewards"].add(arg_value)
    elif predicate == "produces" and arg_key == "object":
        bucket["produces"].add(arg_value)
    elif predicate == "consumes" and arg_key == "object":
        bucket["consumes"].add(arg_value)
    elif predicate == "enables" and arg_key == "capability":
        bucket["enables"].add(arg_value)
    elif predicate == "protects" and arg_key == "target_class":
        bucket["protects"].add(arg_value)
    elif predicate == "answers" and arg_key == "threat_class":
        bucket["answers"].add(arg_value)
    elif predicate == "tutors" and arg_key == "selector":
        bucket["tutors"].add(arg_value)
    elif predicate == "recurs" and arg_key:
        bucket["recurs"].add(f"{arg_key}:{arg_value}")


def pred_sets_from_rows(
    rows: Iterable[tuple[str, str, str, str]],
) -> dict[str, dict[str, set[str]]]:
    """(card_name, predicate, arg_key, arg_value) → bucket sets keyed by lowercased name."""
    by_name: dict[str, dict[str, set[str]]] = {}
    for card_name, predicate, arg_key, arg_value in rows:
        key = str(card_name or "").lower()
        if not key:
            continue
        bucket = by_name.setdefault(key, empty_pred_sets())
        add_predicate_to_sets(bucket, str(predicate or ""), str(arg_key or ""), str(arg_value or ""))
    return by_name


def pred_sets_from_id_rows(
    rows: Iterable[tuple[str, str, str, str]],
) -> dict[str, dict[str, set[str]]]:
    """(card_id, predicate, arg_key, arg_value) → bucket sets keyed by catalog id."""
    by_id: dict[str, dict[str, set[str]]] = {}
    for card_id, predicate, arg_key, arg_value in rows:
        key = str(card_id or "").strip()
        if not key:
            continue
        bucket = by_id.setdefault(key, empty_pred_sets())
        add_predicate_to_sets(bucket, str(predicate or ""), str(arg_key or ""), str(arg_value or ""))
    return by_id


def signature_tokens(
    card_sets: Mapping[str, Iterable[str]] | None,
) -> list[str]:
    """Sorted `bucket:value` tokens. Same skip rules as `predicate_signature`."""
    return sorted(f"{bucket}:{value}" for bucket, value in predicate_signature(card_sets))


def predicate_signature(
    card_sets: Mapping[str, Iterable[str]] | None,
) -> frozenset[tuple[str, str]]:
    """Distinctive (bucket, value) pairs. `produces(mana)` is Stage 3.5's job."""
    if not card_sets:
        return frozenset()
    out: set[tuple[str, str]] = set()
    for bucket, values in card_sets.items():
        for value in values or ():
            text = str(value).strip()
            if not text:
                continue
            key = (str(bucket), text)
            if key in _SIGNATURE_SKIP:
                continue
            out.add(key)
    return frozenset(out)


def signature_redundancy(
    left: Mapping[str, Iterable[str]] | None,
    right: Mapping[str, Iterable[str]] | None,
    left_cmc: float = 0.0,
    right_cmc: float = 0.0,
) -> float:
    """Layer 2: Jaccard of signatures, and only in the same cmc band.

    Empty signatures mean "no ontology claim"; the solver falls back to text.
    Disjoint signatures are 0 even when the Oracle wording overlaps.
    """
    left_sig = predicate_signature(left)
    right_sig = predicate_signature(right)
    if not left_sig or not right_sig:
        return 0.0
    shared = left_sig & right_sig
    if not shared:
        return 0.0
    if cmc_bucket(float(left_cmc or 0)) != cmc_bucket(float(right_cmc or 0)):
        return 0.0
    return len(shared) / len(left_sig | right_sig)


def repair_hit_count(
    card_sets: Mapping[str, set[str]],
    records: Iterable[Mapping],
) -> int:
    """How many deficit records this card's predicates close.

    One hit per record: extra repair signatures on the same row do not stack.
    """
    hits = 0
    for record in records:
        for repair in record.get("repairs") or []:
            predicate = str(repair.get("predicate") or "")
            value = str(repair.get("arg_value") or "")
            if value and value in (card_sets.get(predicate) or set()):
                hits += 1
                break
    return hits


def _by_prefix(counts: Mapping[str, int], prefix: str) -> dict[str, int]:
    head = prefix + ":"
    out: dict[str, int] = {}
    for key, value in counts.items():
        if key.startswith(head):
            name = key[len(head) :]
            if name:
                out[name] = int(value or 0)
    return out


def _repair(predicate: str, arg_key: str, arg_value: str) -> dict[str, str]:
    return {"predicate": predicate, "arg_key": arg_key, "arg_value": arg_value}


def flow_snapshot(counts: Mapping[str, int]) -> dict:
    """Event/object supply vs demand. Mana is Stage 3.5's job, not this table."""
    emits = _by_prefix(counts, "emits")
    rewards = _by_prefix(counts, "rewards")
    produces = _by_prefix(counts, "produces")
    consumes = _by_prefix(counts, "consumes")
    answers = _by_prefix(counts, "answers")
    protects = _by_prefix(counts, "protects")

    orphans = []
    for event, n_reward in sorted(rewards.items()):
        n_emit = int(emits.get(event) or 0)
        if n_reward > 0 and n_emit == 0:
            orphans.append({"event": event, "rewards": n_reward, "emits": 0})

    starved = []
    unmatched = []
    for obj, n_consume in sorted(consumes.items()):
        if obj in _IGNORE_OBJECTS:
            continue
        n_produce = int(produces.get(obj) or 0)
        if n_consume > 0 and n_produce == 0:
            starved.append({"object": obj, "consumes": n_consume, "produces": 0})
    for obj, n_produce in sorted(produces.items()):
        if obj in _IGNORE_OBJECTS:
            continue
        n_consume = int(consumes.get(obj) or 0)
        if n_produce > 0 and n_consume == 0:
            unmatched.append({"object": obj, "produces": n_produce, "consumes": 0})

    matched_events = sorted(
        event
        for event in set(emits) | set(rewards)
        if int(emits.get(event) or 0) > 0 and int(rewards.get(event) or 0) > 0
    )
    return {
        "orphans": orphans,
        "starved": starved,
        "unmatched_producers": unmatched,
        "matched_events": matched_events,
        "answers": answers,
        "protects": protects,
    }


def flow_repair_records(counts: Mapping[str, int]) -> list[dict]:
    """Generic orphan/starved/unmatched rows in the cut/diagnose repair shape."""
    snap = flow_snapshot(counts)
    records: list[dict] = []
    for row in snap["orphans"]:
        event = row["event"]
        records.append(
            {
                "text": (
                    f"{row['rewards']} {event} payoffs, 0 {event} emitters"
                ),
                "kind": "orphan",
                "repairs": [_repair("emits", "event", event)],
            }
        )
    for row in snap["starved"]:
        obj = row["object"]
        records.append(
            {
                "text": (
                    f"{row['consumes']} {obj} consumers, 0 {obj} producers"
                ),
                "kind": "starved",
                "repairs": [_repair("produces", "object", obj)],
            }
        )
    for row in snap["unmatched_producers"]:
        obj = row["object"]
        records.append(
            {
                "text": (
                    f"{row['produces']} {obj} sources, 0 {obj} consumers"
                ),
                "kind": "unmatched",
                "repairs": [_repair("consumes", "object", obj)],
            }
        )
    return records[:_FLOW_CAP]


def merge_deficit_records(
    primary: Iterable[Mapping],
    extra: Iterable[Mapping],
) -> list[dict]:
    """Keep curated rows; add flow rows whose repairs are not already covered."""
    out: list[dict] = []
    covered: set[tuple[str, str]] = set()
    seen_text: set[str] = set()

    def absorb(record: Mapping) -> None:
        text = str(record.get("text") or "")
        if text in seen_text:
            return
        repairs = list(record.get("repairs") or [])
        keys = [
            (str(item.get("predicate") or ""), str(item.get("arg_value") or ""))
            for item in repairs
        ]
        if keys and all(key in covered for key in keys):
            return
        seen_text.add(text)
        out.append(dict(record))
        covered.update(keys)

    for record in primary:
        absorb(record)
    for record in extra:
        absorb(record)
    return out


def search_queries_from_records(records: Iterable[Mapping]) -> list[str]:
    """Explicit `predicate:value` strings for search_cards / Solver retrieve."""
    queries: list[str] = []
    seen: set[str] = set()
    for record in records:
        for repair in record.get("repairs") or []:
            predicate = str(repair.get("predicate") or "").strip()
            value = str(repair.get("arg_value") or "").strip()
            if not predicate or not value:
                continue
            query = f"{predicate}:{value}"
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            queries.append(query)
    return queries
