from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field


COMMAND_ZONE_SIZE = 1
MAIN_DECK_SIZE = 99
DECK_SIZE = COMMAND_ZONE_SIZE + MAIN_DECK_SIZE


def extract_json(text: str) -> dict | None:
    """Parse a JSON object from model output, including fenced blocks."""
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


VALID_INTENTS = ("build", "improve", "substitute", "cut")


def infer_intent(query: str, has_cards: bool = False) -> str:
    """Classify the user task. Improve/substitute are first-class, not failed builds."""
    q = (query or "").lower()
    if any(
        phrase in q
        for phrase in (
            "substitut",
            "replace",
            "swap ",
            "swap in",
            "instead of",
            "cut in",
            "take out",
        )
    ):
        return "substitute"
    if any(
        phrase in q
        for phrase in ("cut the", "trim", "too many", "down to 99", "cut down", "remove the worst")
    ):
        return "cut"
    if any(
        phrase in q
        for phrase in (
            "better card",
            "better cards",
            "upgrade",
            "improve",
            "optimize",
            "suggest",
            "alternative",
            "what should i change",
            "what would you change",
            "tune",
            "fixes for",
        )
    ):
        return "improve"
    if any(
        phrase in q
        for phrase in ("from scratch", "build me a deck", "build a deck", "full deck", "99 cards")
    ):
        return "build"
    return "improve" if has_cards else "build"


def infer_task(query: str, has_cards: bool = False) -> dict:
    intent = infer_intent(query, has_cards)
    q = (query or "").lower()
    owned_only = any(
        phrase in q
        for phrase in (
            "i own",
            "i already own",
            "owned only",
            "cards i own",
            "from my collection",
            "focus on cards i own",
        )
    )
    full_build = intent == "build" and (
        any(
            phrase in q
            for phrase in (
                "full deck",
                "99 cards",
                "complete deck",
                "build me a deck",
                "build a deck",
                "from scratch",
            )
        )
        or bool(re.search(r"\bfull\b.{0,40}\bdeck\b", q))
        or bool(re.search(r"\bbuild\b.{0,80}\bdeck\b", q))
    )
    return {
        "intent": intent,
        "owned_only": owned_only,
        "require_complete": full_build,
    }


def proposal_has_work(proposal: dict | None) -> bool:
    if not proposal:
        return False
    payload = proposal.get("delta") if isinstance(proposal.get("delta"), dict) else proposal
    for key in ("add", "remove", "substitute", "substitutions", "candidate_pool", "pool"):
        if payload.get(key):
            return True
    return bool(proposal.get("buy_list") or proposal.get("candidate_pool"))


def _qty_items(items) -> list[tuple[str, int]]:
    parsed = []
    for item in items or []:
        if isinstance(item, str):
            name, qty = item, 1
        elif isinstance(item, dict):
            name = item.get("name") or item.get("card") or ""
            qty = int(item.get("quantity", 1))
        else:
            continue
        name = str(name).strip()
        if not name or qty == 0:
            continue
        parsed.append((name, qty))
    return parsed


def _clean_qty_map(cards) -> dict[str, int]:
    clean = {}
    for name, qty in (cards or {}).items():
        qty = int(qty)
        if name and qty > 0:
            clean[str(name)] = qty
    return clean


def _substitutions(items) -> list[tuple[str, str, int, str]]:
    parsed = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        out_name = str(
            item.get("out") or item.get("remove") or item.get("from") or item.get("old") or ""
        ).strip()
        in_name = str(
            item.get("in") or item.get("add") or item.get("to") or item.get("new") or ""
        ).strip()
        qty = int(item.get("quantity", 1))
        reason = str(item.get("reason") or "")
        if out_name and in_name and qty > 0:
            parsed.append((out_name, in_name, qty, reason))
    return parsed


@dataclass
class DeckState:
    """First-class Commander deck: commander + 99 slots + budget constraints."""

    commander: str = ""
    cards: dict[str, int] = field(default_factory=dict)
    identity: list[str] = field(default_factory=list)
    owned_only: bool = False
    require_complete: bool = False
    currency: str = "usd"
    max_card_price: float | None = None
    budget_cap: float | None = None
    owned_cost_zero: bool = True
    intent: str = "build"
    last_delta: dict = field(default_factory=dict)
    candidate_pool: dict[str, int] = field(default_factory=dict)

    def slot_count(self) -> int:
        return sum(max(int(qty), 0) for qty in self.cards.values())

    def remaining_slots(self) -> int:
        return max(0, MAIN_DECK_SIZE - self.slot_count())

    def pool_count(self) -> int:
        return sum(max(int(qty), 0) for qty in self.candidate_pool.values())

    def card_list(self) -> dict[str, int]:
        return {name: int(qty) for name, qty in self.cards.items() if int(qty) > 0}

    def _canonical_map(self) -> dict[str, str]:
        return {name.lower(): name for name in self.cards}

    def _key(self, name: str) -> str | None:
        return self._canonical_map().get(name.lower())

    def set_commander(self, name: str):
        name = (name or "").strip()
        self.commander = name
        existing = self._key(name)
        if existing:
            self.cards.pop(existing, None)

    def _pool_key(self, name: str) -> str | None:
        return {n.lower(): n for n in self.candidate_pool}.get(name.lower())

    def add_to_pool(self, name: str, quantity: int = 1):
        name = name.strip()
        if not name or quantity <= 0:
            return
        if self.commander and name.lower() == self.commander.lower():
            return
        key = self._pool_key(name)
        if key:
            self.candidate_pool[key] += quantity
        else:
            self.candidate_pool[name] = quantity

    def take_from_pool(self, name: str, quantity: int = 1) -> int:
        key = self._pool_key(name)
        if not key:
            return 0
        have = self.candidate_pool[key]
        moved = min(quantity, have)
        remaining = have - moved
        if remaining <= 0:
            self.candidate_pool.pop(key)
        else:
            self.candidate_pool[key] = remaining
        return moved

    def add_card(self, name: str, quantity: int = 1):
        name = name.strip()
        if not name or quantity == 0:
            return
        if self.commander and name.lower() == self.commander.lower():
            return
        key = self._key(name)
        if quantity < 0:
            self.remove_card(name, -quantity)
            return
        if key:
            self.cards[key] += quantity
        else:
            self.cards[name] = quantity

    def remove_card(self, name: str, quantity: int = 1):
        key = self._key(name)
        if not key:
            return
        remaining = self.cards[key] - quantity
        if remaining <= 0:
            self.cards.pop(key)
        else:
            self.cards[key] = remaining

    def substitute(self, out_name: str, in_name: str, quantity: int = 1) -> bool:
        """Replace quantity copies of out_name with in_name. No-op if out_name is not in the deck."""
        key = self._key(out_name)
        if not key:
            return False
        qty = min(quantity, self.cards[key])
        if qty <= 0:
            return False
        self.remove_card(out_name, qty)
        self.add_card(in_name, qty)
        return True

    def apply_delta(self, delta: dict | None):
        applied = {
            "added": [],
            "removed": [],
            "substituted": [],
            "failed_substitutions": [],
            "overflow_to_pool": [],
        }
        if not delta:
            self.last_delta = applied
            return self
        intent = delta.get("intent")
        if intent in VALID_INTENTS:
            self.intent = intent
        commander = delta.get("commander")
        if commander:
            self.set_commander(commander)
        identity = delta.get("identity")
        if identity:
            self.identity = list(identity)
        nested = delta.get("delta") if isinstance(delta.get("delta"), dict) else None
        payload = nested or delta
        for out_name, in_name, qty, reason in _substitutions(
            payload.get("substitute") or payload.get("substitutions")
        ):
            ok = self.substitute(out_name, in_name, qty)
            record = {"out": out_name, "in": in_name, "quantity": qty, "reason": reason}
            if ok:
                applied["substituted"].append(record)
            else:
                applied["failed_substitutions"].append(record)
        for name, qty in _qty_items(payload.get("remove")):
            self.remove_card(name, qty)
            applied["removed"].append({"name": name, "quantity": qty})
        for name, qty in _qty_items(payload.get("add")):
            room = self.remaining_slots()
            into_deck = min(qty, room)
            overflow = qty - into_deck
            if into_deck:
                self.add_card(name, into_deck)
                applied["added"].append({"name": name, "quantity": into_deck})
            if overflow:
                self.add_to_pool(name, overflow)
                applied["overflow_to_pool"].append({"name": name, "quantity": overflow})
        for name, qty in _qty_items(
            payload.get("candidate_pool")
            or delta.get("candidate_pool")
            or payload.get("pool")
        ):
            self.add_to_pool(name, qty)
        self.last_delta = applied
        return self

    def to_dict(self) -> dict:
        data = asdict(self)
        data["slot_count"] = self.slot_count()
        data["complete"] = self.slot_count() == MAIN_DECK_SIZE and bool(self.commander)
        return data

    def summary(self) -> dict:
        return {
            "commander": self.commander,
            "identity": self.identity,
            "slot_count": self.slot_count(),
            "target_slots": MAIN_DECK_SIZE,
            "remaining_slots": self.remaining_slots(),
            "owned_only": self.owned_only,
            "require_complete": self.require_complete,
            "currency": self.currency,
            "max_card_price": self.max_card_price,
            "budget_cap": self.budget_cap,
            "owned_cost_zero": self.owned_cost_zero,
            "intent": self.intent,
            "cards": self.card_list(),
            "candidate_pool": {name: qty for name, qty in self.candidate_pool.items() if qty > 0},
            "last_delta": self.last_delta,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> DeckState:
        data = data or {}
        deck = cls(
            commander=data.get("commander") or "",
            cards=_clean_qty_map(data.get("cards")),
            identity=list(data.get("identity") or []),
            owned_only=bool(data.get("owned_only", False)),
            require_complete=bool(data.get("require_complete", False)),
            currency=data.get("currency") or "usd",
            max_card_price=data.get("max_card_price"),
            budget_cap=data.get("budget_cap"),
            owned_cost_zero=bool(data.get("owned_cost_zero", True)),
            intent=data.get("intent") if data.get("intent") in VALID_INTENTS else "build",
            candidate_pool=_clean_qty_map(data.get("candidate_pool")),
        )
        if deck.commander:
            deck.set_commander(deck.commander)
        return deck

    @classmethod
    def from_inventory_location(
        cls,
        commander: str,
        location: str,
        db_path: str | None = None,
        **kwargs,
    ) -> DeckState:
        from inventory import DB_NAME, list_inventory

        cards = {}
        for row in list_inventory(location, db_path or DB_NAME):
            cards[row["card_name"]] = row["quantity"]
        kwargs.setdefault("intent", "improve")
        deck = cls(commander=commander, cards=cards, **kwargs)
        if commander:
            deck.set_commander(commander)
            if not deck.identity:
                from catalog import get_oracle_card
                info = get_oracle_card(commander, db_path or DB_NAME)
                if info:
                    deck.identity = list(info["color_identity"])
        return deck

    @classmethod
    def from_proposal(cls, proposal: dict | None, base: DeckState | None = None) -> DeckState:
        deck = cls.from_dict(base.to_dict() if base else {})
        if proposal:
            deck.apply_delta(proposal)
        return deck
