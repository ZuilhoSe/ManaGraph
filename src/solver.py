from __future__ import annotations

import re

from catalog import (
    DB_NAME,
    acquisition_cost,
    card_unit_price,
    enrich_deck,
    get_oracle_card,
)
from deck_state import MAIN_DECK_SIZE, DeckState
from inventory import get_card as get_inventory_card
from roles import ROLE_QUOTAS, classify_roles, role_counts, role_need_bonus
from rules_validator import (
    ILLEGAL_COMMANDER_STATUS,
    allows_any_number,
    commander_format_status,
    is_basic_land,
)

STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "for", "on", "in", "with", "your",
    "you", "this", "that", "it", "is", "when", "whenever", "may", "can", "into",
}

IDENTITY_BASICS = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}

ROLE_QUERIES = (
    "add mana ramp artifact",
    "draw a card",
    "destroy target exile counter",
    "creature tokens",
    "basic land",
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower())) - STOPWORDS


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _curve_bucket(cmc: float) -> float | str:
    if cmc >= 7:
        return "7+"
    return str(int(cmc))


class DeckSolver:
    """Greedy fill/cut. Geometry score is text overlap; Chroma distance is optional."""

    def __init__(self, db_path: str = DB_NAME, searcher=None):
        self.db_path = db_path
        self.searcher = searcher
        self._oracle: dict[str, dict | None] = {}
        self._owned: dict[str, int] = {}
        self._ctx: dict | None = None

    def _info(self, name: str | None) -> dict | None:
        if not name:
            return None
        key = name.lower()
        if key not in self._oracle:
            self._oracle[key] = get_oracle_card(name, self.db_path)
        return self._oracle[key]

    def _owned_qty(self, name: str) -> int:
        key = name.lower()
        if key not in self._owned:
            inv = get_inventory_card(name, self.db_path)
            self._owned[key] = inv["total_quantity"] if inv else 0
        return self._owned[key]

    def _rebuild_context(self, deck: DeckState, query: str = ""):
        cmd = self._info(deck.commander) if deck.commander else None
        deck_cards = []
        other_toks = []
        curve = {str(i): 0 for i in range(0, 7)}
        curve["7+"] = 0
        for name, qty in deck.card_list().items():
            info = self._info(name) or {
                "name": name,
                "type_line": "",
                "oracle_text": "",
                "cmc": 0,
            }
            deck_cards.append({**info, "quantity": qty})
            other_toks.append(
                _tokens(f"{info.get('name','')} {info.get('type_line','')} {info.get('oracle_text','')}")
            )
            tl = (info.get("type_line") or "").lower()
            if "land" not in tl:
                bucket = _curve_bucket(float(info.get("cmc") or 0))
                curve[bucket] = curve.get(bucket, 0) + qty
        budget = 0.0
        if deck.budget_cap is not None:
            budget = enrich_deck(deck, self.db_path)["budget_used"]
        self._ctx = {
            "cmd": cmd,
            "target": _tokens(
                " ".join(
                    filter(
                        None,
                        [query, (cmd or {}).get("oracle_text", ""), (cmd or {}).get("name", "")],
                    )
                )
            ),
            "deck_cards": deck_cards,
            "counts": role_counts(deck_cards),
            "other_toks": other_toks,
            "curve": curve,
            "budget": budget,
        }

    def fill(
        self,
        deck: DeckState,
        query: str = "",
        extra_candidates: list[str] | None = None,
        retrieve: bool = False,
        max_adds: int | None = None,
    ) -> dict:
        if not deck.commander:
            return {"ok": False, "error": "No commander set.", "added": []}
        if not deck.identity:
            cmd = self._info(deck.commander)
            if cmd:
                deck.identity = list(cmd["color_identity"])

        candidates = self._gather_names(deck, query, extra_candidates, retrieve)
        print(f"[Solver] fill: {len(candidates)} candidates, {deck.remaining_slots()} slots")
        added = []
        skipped = []
        dead = set()
        cap = max_adds if max_adds is not None else deck.remaining_slots()
        self._rebuild_context(deck, query)

        while deck.remaining_slots() > 0 and cap > 0:
            ranked = []
            for name in candidates:
                if name.lower() in dead:
                    continue
                ok, reason = self.can_add(deck, name)
                if not ok:
                    dead.add(name.lower())
                    skipped.append({"name": name, "reason": reason})
                    continue
                ranked.append((self.score_candidate(deck, name, query), name))
            ranked.sort(reverse=True)
            if not ranked:
                basic = self._best_basic(deck)
                if not basic or basic.lower() in dead:
                    break
                ok, _reason = self.can_add(deck, basic)
                if not ok:
                    break
                self._commit_add(deck, basic, 1)
                added.append({"name": basic, "score": 0.0, "source": "basic"})
                cap -= 1
                self._rebuild_context(deck, query)
                continue
            score, name = ranked[0]
            self._commit_add(deck, name, 1)
            added.append({"name": name, "score": round(score, 4), "source": "greedy"})
            cap -= 1
            info = self._info(name) or {}
            if not allows_any_number(info.get("oracle_text") or "") and not is_basic_land(
                info.get("type_line") or ""
            ):
                dead.add(name.lower())
            self._rebuild_context(deck, query)
            if len(added) % 10 == 0:
                print(f"[Solver] filled {len(added)} / slots={deck.slot_count()}")

        return {
            "ok": True,
            "added": added,
            "skipped": skipped[:40],
            "slot_count": deck.slot_count(),
            "remaining_slots": deck.remaining_slots(),
            "pool_count": deck.pool_count(),
        }

    def cut(self, deck: DeckState, query: str = "", max_swaps: int = 12) -> dict:
        if not deck.commander:
            return {"ok": False, "error": "No commander set.", "removed": [], "swapped": []}
        if not deck.identity:
            cmd = self._info(deck.commander)
            if cmd:
                deck.identity = list(cmd["color_identity"])

        removed = []
        swapped = []
        self._rebuild_context(deck, query)

        while True:
            if deck.budget_cap is None or (self._ctx or {}).get("budget", 0) <= deck.budget_cap:
                break
            victim = self._worst_cut(deck, query, prefer_expensive=True)
            if not victim:
                break
            deck.remove_card(victim, 1)
            deck.add_to_pool(victim, 1)
            removed.append({"name": victim, "reason": "over_budget"})
            self._rebuild_context(deck, query)

        while deck.slot_count() > MAIN_DECK_SIZE:
            victim = self._worst_cut(deck, query, prefer_expensive=False)
            if not victim:
                break
            deck.remove_card(victim, 1)
            deck.add_to_pool(victim, 1)
            removed.append({"name": victim, "reason": "over_99"})
            self._rebuild_context(deck, query)

        for _ in range(max_swaps):
            if not deck.candidate_pool or deck.slot_count() == 0:
                break
            worst = self._worst_cut(deck, query, prefer_expensive=False)
            if not worst:
                break
            trial = DeckState.from_dict(deck.to_dict())
            trial.remove_card(worst, 1)
            self._rebuild_context(trial, query)
            best = self._best_pool_card(trial, query)
            if not best or worst.lower() == best.lower():
                self._rebuild_context(deck, query)
                break
            # Compare on the same trial context: would `best` beat putting `worst` back?
            if self.score_candidate(trial, best, query) <= self.score_candidate(trial, worst, query):
                self._rebuild_context(deck, query)
                break
            ok, _reason = self.can_add(trial, best)
            if not ok:
                deck.take_from_pool(best, 1)
                self._rebuild_context(deck, query)
                continue
            deck.remove_card(worst, 1)
            deck.add_to_pool(worst, 1)
            self._commit_add(deck, best, 1)
            swapped.append({"out": worst, "in": best})
            self._rebuild_context(deck, query)

        return {
            "ok": True,
            "removed": removed,
            "swapped": swapped,
            "slot_count": deck.slot_count(),
            "pool_count": deck.pool_count(),
        }

    def can_add(self, deck: DeckState, name: str, quantity: int = 1) -> tuple[bool, str]:
        if deck.remaining_slots() < quantity:
            return False, "no remaining slots"
        info = self._info(name)
        if not info:
            return False, "unknown card"
        if info["name"].lower() == (deck.commander or "").lower():
            return False, "commander"
        identity = set(deck.identity or [])
        if not set(info["color_identity"]).issubset(identity):
            return False, "color identity"
        status = commander_format_status(info)
        if status in ILLEGAL_COMMANDER_STATUS:
            return False, f"format {status}"
        current = 0
        key = deck._key(info["name"]) or deck._key(name)
        if key:
            current = deck.cards[key]
        new_qty = current + quantity
        if new_qty > 1 and not is_basic_land(info["type_line"]) and not allows_any_number(info["oracle_text"]):
            return False, "singleton"
        owned_qty = self._owned_qty(info["name"])
        if deck.owned_only and owned_qty < new_qty:
            return False, "not owned"
        unit = card_unit_price(info, deck.currency)
        cost, _owned_used, buy_qty, unknown = acquisition_cost(
            quantity, max(owned_qty - current, 0), unit, deck.owned_cost_zero
        )
        if deck.max_card_price is not None and buy_qty > 0:
            if unit is None:
                return False, "unknown price"
            if unit > deck.max_card_price:
                return False, "over P_max"
        if deck.budget_cap is not None:
            if unknown:
                return False, "unknown price"
            used = (self._ctx or {}).get("budget")
            if used is None:
                used = enrich_deck(deck, self.db_path)["budget_used"]
            if cost is not None and used + cost > deck.budget_cap:
                return False, "over budget"
        return True, "ok"

    def score_candidate(self, deck: DeckState, name: str, query: str = "") -> float:
        info = self._info(name)
        if not info:
            return -999.0
        if self._ctx is None:
            self._rebuild_context(deck, query)
        ctx = self._ctx
        cmd = ctx["cmd"]
        target = ctx["target"]
        card_tok = _tokens(f"{info['name']} {info['type_line']} {info['oracle_text']}")
        synergy = _jaccard(target, card_tok)
        distance = info.get("_distance")
        if distance is not None:
            synergy = max(synergy, 1.0 / (1.0 + float(distance)))

        counts = ctx["counts"]
        roles = classify_roles(info["type_line"], info["oracle_text"])
        role_score = max((role_need_bonus(role, counts) for role in roles), default=0.0)

        redundancy = 0.0
        for other, other_tok in zip(ctx["deck_cards"], ctx["other_toks"]):
            overlap = _jaccard(card_tok, other_tok)
            if roles & classify_roles(other.get("type_line") or "", other.get("oracle_text") or ""):
                overlap *= 1.25
            redundancy = max(redundancy, overlap)

        if "land" not in (info.get("type_line") or "").lower():
            bucket = _curve_bucket(float(info.get("cmc") or 0))
            if ctx["curve"].get(bucket, 0) >= 18:
                role_score -= 0.8

        unit = card_unit_price(info, deck.currency) or 0.0
        value = synergy / (unit + 0.5) if deck.budget_cap is not None else 0.0
        return 2.0 * synergy + role_score - 1.4 * redundancy + value

    def _commit_add(self, deck: DeckState, name: str, quantity: int):
        deck.add_card(name, quantity)
        deck.take_from_pool(name, quantity)

    def _deck_card_infos(self, deck: DeckState) -> list[dict]:
        if self._ctx:
            return self._ctx["deck_cards"]
        cards = []
        for name, qty in deck.card_list().items():
            info = self._info(name) or {"name": name, "type_line": "", "oracle_text": ""}
            cards.append({**info, "quantity": qty})
        return cards

    def _worst_cut(self, deck: DeckState, query: str, prefer_expensive: bool) -> str | None:
        if self._ctx is None:
            self._rebuild_context(deck, query)
        deck_cards = self._ctx["deck_cards"]
        counts = self._ctx["counts"]
        land_count = counts.get("land", 0)
        worst_name = None
        worst_score = None
        for card in deck_cards:
            name = card["name"]
            roles = classify_roles(card.get("type_line") or "", card.get("oracle_text") or "")
            if "land" in roles and land_count <= ROLE_QUOTAS["land"][0]:
                continue
            protected = False
            for role in roles:
                if role in ROLE_QUOTAS and counts.get(role, 0) <= ROLE_QUOTAS[role][0]:
                    protected = True
                    break
            if protected and not prefer_expensive:
                continue
            fill_score = self.score_candidate(deck, name, query)
            unit = card_unit_price(card, deck.currency) or 0.0
            cut_score = -fill_score + (unit if prefer_expensive else 0.0)
            if worst_score is None or cut_score > worst_score:
                worst_score = cut_score
                worst_name = name
        return worst_name

    def _best_pool_card(self, deck: DeckState, query: str) -> str | None:
        best_name = None
        best_score = None
        for name in list(deck.candidate_pool):
            ok, _reason = self.can_add(deck, name)
            if not ok:
                continue
            score = self.score_candidate(deck, name, query)
            if best_score is None or score > best_score:
                best_score = score
                best_name = name
        return best_name

    def _best_basic(self, deck: DeckState) -> str | None:
        colors = deck.identity or []
        names = [IDENTITY_BASICS[c] for c in colors if c in IDENTITY_BASICS]
        if not names:
            names = ["Wastes"]
        for name in names:
            info = self._info(name)
            if info:
                return info["name"]
        return None

    def _gather_names(
        self,
        deck: DeckState,
        query: str,
        extra_candidates: list[str] | None,
        retrieve: bool,
    ) -> list[str]:
        names: list[str] = []
        seen = set()

        def push(name: str):
            if not name:
                return
            key = name.lower()
            if key in seen:
                return
            if deck.commander and key == deck.commander.lower():
                return
            seen.add(key)
            names.append(name)

        for name in deck.candidate_pool:
            push(name)
        for name in extra_candidates or []:
            push(name)
        if retrieve:
            for name in self._retrieve(deck, query):
                push(name)
        return names

    def _retrieve(self, deck: DeckState, query: str) -> list[str]:
        try:
            searcher = self.searcher
            if searcher is None:
                from hybrid_search import RAGSearcher
                searcher = RAGSearcher()
                self.searcher = searcher
        except Exception as exc:
            print(f"[Solver] retrieval unavailable ({type(exc).__name__}: {exc})")
            return []
        cmd = self._info(deck.commander) if deck.commander else None
        queries = [q for q in [query, (cmd or {}).get("oracle_text"), (cmd or {}).get("name")] if q]
        if cmd and "—" in (cmd.get("type_line") or ""):
            queries.append(cmd["type_line"].split("—", 1)[1].strip() + " creature")
        queries.extend(ROLE_QUERIES)
        colors = list(deck.identity or (cmd or {}).get("color_identity") or [])
        found = []
        for q in queries[:8]:
            try:
                hits = searcher.search_cards(
                    query=q,
                    allowed_colors=colors,
                    owned_only=deck.owned_only,
                    limit=40,
                    max_card_price=deck.max_card_price,
                    currency=deck.currency,
                    n_results=160,
                )
            except Exception as exc:
                print(f"[Solver] search failed for '{q}': {exc}")
                continue
            for hit in hits:
                info = self._info(hit["name"])
                if info is not None and hit.get("distance") is not None:
                    prev = info.get("_distance")
                    if prev is None or hit["distance"] < prev:
                        info["_distance"] = hit["distance"]
                found.append(hit["name"])
        print(f"[Solver] retrieved {len(found)} hits ({len(set(n.lower() for n in found))} unique)")
        return found
