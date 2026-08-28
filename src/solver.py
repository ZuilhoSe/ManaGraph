from __future__ import annotations

import os
import re
import sqlite3

from catalog import (
    DB_NAME,
    _row_to_card,
    acquisition_cost,
    card_unit_price,
    enrich_deck,
    ensure_schema,
    get_oracle_card,
)
from archetypes import (
    infer_archetype,
    planned_roles,
    profile_for,
    quotas_for,
    search_queries_for,
)
from catalog_filters import (
    identity_ok,
    is_commander_legal,
    names_by_type_fragment,
    type_line_has_fragment,
)
from deck_state import MAIN_DECK_SIZE, DeckState, _normalize_key
from geometry import cosine, load_card_views, multi_view_cosine
from inventory import get_card as get_inventory_card
from mana import cmc_bucket, diagnose, produces_mana, shape_bonus, strategy_from_name
from roles import ROLE_QUOTAS, role_counts, role_need_bonus, token_classes
from symbolic_cards import classify_card, requirement_families
from rules_validator import (
    ILLEGAL_COMMANDER_STATUS,
    allows_any_number,
    commander_format_status,
    is_basic_land,
)

# Soft preference for lands whose type_line matches preferred_land_types.
PREFERRED_LAND_BONUS = 1.2
THEME_TYPE_BONUS = 0.9
# Leave room for Command Tower / fixers when not land_types_strict.
PREFERRED_LAND_FIXER_RESERVE = 6
# Caps so filters do not dump the entire Gate/Room catalog into a 99.
PREFERRED_LAND_COMMIT_CAP = 28
THEME_POOL_CAP = 14  # max theme cards seeded into the pool per fragment
THEME_SOFT_CAP = 12  # theme score bonus stops here
THEME_HARD_CAP = 16  # further theme cards are skipped
ARCHETYPE_GLUE_CAP = 56  # non-land oracle matches when retrieval is thin/unavailable
ARCHETYPE_GLUE_PER_PHRASE = 12
# Soft mana-base fixers seeded when preferred_land_types is set (not strict).
PREFERRED_LAND_FIXERS = (
    "Command Tower",
    "Exotic Orchard",
    "City of Brass",
    "Mana Confluence",
    "Reflecting Pool",
    "Plaza of Heroes",
    "The World Tree",
    "Path of Ancestry",
    "Evolving Wilds",
    "Terramorphic Expanse",
    "Myriad Landscape",
    "Reliquary Tower",
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

# Known non-tribal deck themes: if the literal keyword shows up anywhere in the
# commander's oracle_text, fire the theme's queries into retrieval. Only 3 for
# now -- the ones we've actually seen misfire without this (see
# eval/archetypes/aminatou_esper_enchantments.json). Add more as they come up.
#
# "enchantment" is down to its one query that's actually earned its keep on a
# benchmark (see eval-harness-plan.md) -- bare "enchantment", constellation,
# and eerie variants were cut for bringing zero good hits. artifact/graveyard
# haven't been benchmarked yet, so treat those as unproven.
THEME_QUERIES = {
    # Short oracle-shaped templates: one long NL query missed Mesa Enchantress /
    # Entity Tracker / Ashiok's Reaper because their wording differs.
    "enchantment": (
        "whenever you cast an enchantment spell",
        "enchantment you control enters",
        "whenever an enchantment you control",
    ),
    "artifact": (
        "artifact",
        "artifact synergy when artifact enters the battlefield",
    ),
    "graveyard": (
        "graveyard",
        "graveyard recursion reanimate self mill",
    ),
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower())) - STOPWORDS


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def creature_types(type_line: str) -> set[str]:
    """Subtype words on any face that is a Creature (DFC-safe)."""
    found: set[str] = set()
    for face in (type_line or "").split("//"):
        if "creature" not in face.lower():
            continue
        subtype = ""
        for sep in ("—", "–", " - "):
            if sep in face:
                subtype = face.split(sep, 1)[1]
                break
        found.update(re.findall(r"[a-z]+", subtype.lower()))
    return found


def is_creature_card(type_line: str) -> bool:
    return any("creature" in face.lower() for face in (type_line or "").split("//"))


def self_referential_types(cmd: dict | None) -> set[str]:
    """Which of the commander's own creature-type words its own oracle_text
    mentions -- e.g. Krenko is a Goblin and his text says "Goblins you
    control...", so Goblin is a real tribal signal. Aminatou is a Human
    Wizard but her text never says "human" or "wizard" -- being a creature
    type isn't the same as a deck being built around it, so no tribal signal
    fires for her. This is the single gate for the tribal bonus/penalty
    (_tribe_adj/_theme_match), the off-tribe hard filter (_off_tribe_creature),
    and the auto-generated tribal search query in _retrieve -- a commander
    that fails this check gets none of the three."""
    if not cmd:
        return set()
    types = creature_types(cmd.get("type_line") or "")
    if not types:
        return set()
    text = (cmd.get("oracle_text") or "").lower()
    return {t for t in types if re.search(rf"\b{re.escape(t)}s?\b", text)}


def detect_known_themes(cmd: dict | None) -> list[str]:
    """Keyword-only theme detection: fires a THEME_QUERIES entry if its literal
    name appears anywhere in the commander's oracle_text. Accepted risk, not
    fixed: no polarity check, so a commander whose only mention is "destroy
    target enchantment" fires the enchantment theme exactly like one that says
    "whenever you cast an enchantment, draw a card". Cheap to accept because a
    misfire only adds a couple of extra retrieval queries to a pool of
    hundreds of candidates -- it's not a hard filter like the tribal one."""
    if not cmd:
        return []
    text = (cmd.get("oracle_text") or "").lower()
    return [theme for theme in THEME_QUERIES if re.search(rf"\b{theme}s?\b", text)]


CHROMA_JACCARD_GATE = 0.12
TRIBE_MATCH_BONUS = 1.0
TRIBE_MISS_PENALTY = -1.6
TOKEN_ALIGN_BONUS = 0.8


class DeckSolver:
    """Greedy fill/cut. Geometry score is text overlap; Chroma distance is optional."""

    def __init__(self, db_path: str = DB_NAME, searcher=None):
        self.db_path = db_path
        self.searcher = searcher
        self._oracle: dict[str, dict | None] = {}
        self._owned: dict[str, int] = {}
        self._baseline_lookup: dict[str, int] | None = None
        self._ctx: dict | None = None
        self._emb: dict[str, object] = {}
        self._views: dict[str, dict] = {}
        self._view_store: dict | None | bool = False  # False = not loaded yet

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

    def _pool_norm(self, deck: DeckState) -> frozenset[str] | None:
        """Normalized card_pool names, or None when the deck isn't pool-restricted."""
        if not deck.pool_only or not deck.card_pool:
            return None
        return frozenset(_normalize_key(n) for n in deck.card_pool)

    def _plan(self, deck: DeckState, query: str = "") -> str:
        from_query = infer_archetype(query)
        if from_query != "generic":
            return from_query
        return getattr(deck, "archetype", None) or "generic"

    def _rebuild_context(self, deck: DeckState, query: str = ""):
        cmd = self._info(deck.commander) if deck.commander else None
        arch = self._plan(deck, query)
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
                bucket = cmc_bucket(float(info.get("cmc") or 0))
                curve[bucket] = curve.get(bucket, 0) + qty
        budget = 0.0
        if deck.budget_cap is not None:
            budget = enrich_deck(deck, self.db_path)["budget_used"]
        mana = diagnose(
            deck_cards,
            commander=cmd,
            identity=deck.identity or (cmd or {}).get("color_identity"),
            remaining_slots=deck.remaining_slots(),
            slot_count=deck.slot_count(),
            budget_cap=deck.budget_cap,
            budget_used=budget if deck.budget_cap is not None else None,
            archetype=arch,
            strategy=strategy_from_name(deck.mana_strategy),
        )
        self._ctx = {
            "cmd": cmd,
            "archetype": arch,
            "profile": profile_for(arch),
            "quotas": quotas_for(arch),
            "target": _tokens(
                " ".join(
                    filter(
                        None,
                        [query, (cmd or {}).get("oracle_text", ""), (cmd or {}).get("name", "")],
                    )
                )
            ),
            "deck_cards": deck_cards,
            "counts": role_counts(deck_cards, arch),
            "other_toks": other_toks,
            "curve": curve,
            "budget": budget,
            "mana": mana,
        }

    def _matches_preferred_land(self, info: dict, deck: DeckState) -> bool:
        tl = info.get("type_line") or ""
        if "land" not in tl.lower():
            return False
        return any(
            type_line_has_fragment(tl, frag) for frag in (deck.preferred_land_types or [])
        )

    def _matches_theme_type(self, info: dict, deck: DeckState) -> bool:
        tl = info.get("type_line") or ""
        return any(type_line_has_fragment(tl, frag) for frag in (deck.theme_types or []))

    def _land_count(self, deck: DeckState) -> int:
        n = 0
        for name, qty in deck.card_list().items():
            info = self._info(name) or {}
            if "land" in (info.get("type_line") or "").lower():
                n += int(qty)
        return n

    def _theme_count(self, deck: DeckState, fragment: str | None = None) -> int:
        frags = [fragment] if fragment else list(deck.theme_types or [])
        if not frags:
            return 0
        n = 0
        for name, qty in deck.card_list().items():
            info = self._info(name) or {}
            tl = info.get("type_line") or ""
            if any(type_line_has_fragment(tl, frag) for frag in frags):
                n += int(qty)
        return n

    def _pool_if_new(self, deck: DeckState, name: str) -> bool:
        if not name or deck._key(name) or deck._pool_key(name):
            return False
        if deck.commander and name.lower() == deck.commander.lower():
            return False
        before = deck.pool_count()
        deck.add_to_pool(name, 1)
        return deck.pool_count() > before

    def _seed_archetype_glue(self, deck: DeckState) -> int:
        """Seed non-land staples: profile names first, then tight oracle phrases.

        Broad oracle LIKE without Chroma previously flooded Un-/Alchemy junk and
        left filter builds as Gates+Rooms+Swamps.
        """
        if deck.intent != "build":
            return 0
        identity = list(deck.identity or [])
        profile = profile_for(deck.archetype)
        added = 0

        for name in profile.staple_names:
            if added >= ARCHETYPE_GLUE_CAP:
                break
            info = self._info(name)
            if not info:
                continue
            if "land" in (info.get("type_line") or "").lower():
                continue
            if not identity_ok(info.get("color_identity"), identity):
                continue
            if not is_commander_legal(info.get("legalities")):
                continue
            if self._pool_if_new(deck, info.get("name") or name):
                added += 1

        phrases = list(profile.search_queries)
        if (deck.archetype or "") == "enchantments":
            phrases.extend(
                (
                    "enchantress",
                    "whenever an enchantment enters the battlefield",
                    "constellation —",
                )
            )

        if not os.path.exists(self.db_path):
            if added:
                print(f"[Solver] archetype glue: pool+={added} (archetype={deck.archetype})")
            return added

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            ensure_schema(conn)
            for phrase in phrases:
                if added >= ARCHETYPE_GLUE_CAP:
                    break
                token = (phrase or "").strip()
                if len(token) < 8:
                    continue
                rows = conn.execute(
                    """
                    SELECT * FROM cards
                    WHERE oracle_text LIKE ? COLLATE NOCASE
                      AND type_line NOT LIKE '%Land%' COLLATE NOCASE
                      AND name NOT LIKE 'A-%'
                      AND type_line NOT LIKE '%Attraction%'
                      AND type_line NOT LIKE '%Sticker%'
                    ORDER BY cmc, name
                    LIMIT ?
                    """,
                    (f"%{token}%", ARCHETYPE_GLUE_PER_PHRASE * 2),
                ).fetchall()
                per = 0
                for row in rows:
                    if added >= ARCHETYPE_GLUE_CAP or per >= ARCHETYPE_GLUE_PER_PHRASE:
                        break
                    card = _row_to_card(row)
                    if not identity_ok(card.get("color_identity"), identity):
                        continue
                    if not is_commander_legal(card.get("legalities")):
                        continue
                    name = card.get("name") or ""
                    if self._pool_if_new(deck, name):
                        added += 1
                        per += 1
        finally:
            conn.close()
        if added:
            print(f"[Solver] archetype glue: pool+={added} (archetype={deck.archetype})")
        return added

    def _seed_catalog_filters(self, deck: DeckState) -> dict:
        """Expand preferred_land_types / theme_types from the catalog into the pool.

        Caps theme dump and preferred-land commit so fill still has room for
        staples/glue. Seeds archetype oracle glue when building.
        """
        report = {"theme_pool": 0, "land_pool": 0, "land_committed": 0, "glue": 0}
        if not deck.commander:
            return report
        if not deck.theme_types and not deck.preferred_land_types and not deck.require_complete:
            return report
        if not deck.identity:
            cmd = self._info(deck.commander)
            if cmd:
                deck.identity = list(cmd["color_identity"])
        identity = list(deck.identity or [])

        for frag in deck.theme_types or []:
            matches = names_by_type_fragment(
                frag, identity, lands_only=False, db_path=self.db_path
            )
            scored = []
            for name in matches:
                info = self._info(name) or {}
                scored.append((float(info.get("cmc") or 0), name))
            scored.sort()
            for _cmc, name in scored[:THEME_POOL_CAP]:
                if self._pool_if_new(deck, name):
                    report["theme_pool"] += 1

        preferred: list[str] = []
        for frag in deck.preferred_land_types or []:
            for name in names_by_type_fragment(
                frag, identity, lands_only=True, db_path=self.db_path
            ):
                preferred.append(name)
                if self._pool_if_new(deck, name):
                    report["land_pool"] += 1

        if deck.preferred_land_types and not deck.land_types_strict:
            for name in PREFERRED_LAND_FIXERS:
                if self._pool_if_new(deck, name):
                    report["land_pool"] += 1

        if deck.intent == "build" and preferred:
            land_low = int(ROLE_QUOTAS["land"][0])
            current = self._land_count(deck)
            reserve = 0 if deck.land_types_strict else PREFERRED_LAND_FIXER_RESERVE
            want = max(0, land_low - reserve - current)
            want = min(
                want,
                max(0, PREFERRED_LAND_COMMIT_CAP - current),
                deck.remaining_slots(),
            )
            for name in preferred:
                if want <= 0:
                    break
                if deck._key(name):
                    continue
                ok, _reason = self.can_add(deck, name)
                if not ok:
                    continue
                info = self._info(name) or {}
                if self._skip_reason(info, deck):
                    continue
                self._commit_add(deck, name, 1)
                report["land_committed"] += 1
                want -= 1

        if deck.intent == "build" and (
            deck.theme_types or deck.preferred_land_types or deck.require_complete
        ):
            report["glue"] = self._seed_archetype_glue(deck)

        if (
            report["theme_pool"]
            or report["land_pool"]
            or report["land_committed"]
            or report["glue"]
        ):
            print(
                f"[Solver] catalog filters: theme_pool+={report['theme_pool']} "
                f"land_pool+={report['land_pool']} committed_lands={report['land_committed']} "
                f"glue+={report['glue']}"
            )
        return report

    def fill(
        self,
        deck: DeckState,
        query: str = "",
        extra_candidates: list[str] | None = None,
        retrieve: bool = False,
        max_adds: int | None = None,
        complete_fallback: bool = False,
    ) -> dict:
        if not deck.commander:
            return {"ok": False, "error": "No commander set.", "added": []}
        if not deck.identity:
            cmd = self._info(deck.commander)
            if cmd:
                deck.identity = list(cmd["color_identity"])

        self._seed_catalog_filters(deck)
        candidates = self._gather_names(deck, query, extra_candidates, retrieve)
        print(f"[Solver] fill: {len(candidates)} candidates, {deck.remaining_slots()} slots")
        added = []
        skipped = []
        dead = set()
        cap = max_adds if max_adds is not None else deck.remaining_slots()
        self._rebuild_context(deck, query)
        self._warm_embeddings([deck.commander, *candidates])

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
                info = self._info(name) or {}
                skip = self._skip_reason(info, deck)
                if skip:
                    dead.add(name.lower())
                    skipped.append({"name": name, "reason": skip})
                    continue
                ranked.append((self.score_candidate(deck, name, query), name))
            ranked.sort(reverse=True)
            if not ranked:
                # Never pad the 99 with basics once lands are at quota — that is
                # how filter-only builds used to dump 40+ Swamps.
                if (
                    self._land_count(deck) >= int(ROLE_QUOTAS["land"][0])
                    and not complete_fallback
                ):
                    break
                basic = self._best_basic(deck, allow_overquota=complete_fallback)
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
        self._warm_embeddings(
            [deck.commander, *deck.card_list().keys(), *deck.candidate_pool.keys()]
        )

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

        protected_names = self._freshly_touched_names(deck)
        for _ in range(max_swaps):
            if not deck.candidate_pool or deck.slot_count() == 0:
                break
            worst = self._worst_cut(deck, query, prefer_expensive=False, protected_names=protected_names)
            if not worst:
                break
            trial = DeckState.from_dict(deck.to_dict())
            trial.remove_card(worst, 1)
            self._rebuild_context(trial, query)
            best = self._best_pool_card(trial, query)
            if not best or worst.lower() == best.lower():
                self._rebuild_context(deck, query)
                break
            best_info = self._info(best) or {}
            if self._skip_reason(best_info, deck):
                deck.take_from_pool(best, 1)
                self._rebuild_context(deck, query)
                continue
            # Compare on the same trial context: would `best` beat putting `worst` back?
            if self.score_candidate(trial, best, query) <= self.score_candidate(trial, worst, query):
                self._rebuild_context(deck, query)
                break
            if not self._swap_ok(self._info(worst) or {}, self._info(best) or {}):
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

    def _swap_ok(self, worst: dict, best: dict) -> bool:
        ctx = self._ctx or {}
        profile = ctx.get("profile") or profile_for("generic")
        quotas = ctx.get("quotas") or quotas_for(ctx.get("archetype"))
        counts = ctx.get("counts") or {}
        worst_roles = self._card_roles(worst)
        best_roles = self._card_roles(best)
        for role in profile.protect_roles:
            high = (quotas.get(role) or (0, 0))[1]
            if role in worst_roles and role not in best_roles and counts.get(role, 0) <= high:
                return False
        return True

    def _baseline_qty(self, deck: DeckState, canonical_name: str) -> int:
        """How many copies of `canonical_name` were already in the deck before this
        run started -- exempt from max_card_price under price_cap_new_only, same
        baseline-name matching rules_validator uses for the final gate. Computed once
        per DeckSolver instance: baseline_cards never changes mid-run, and (like
        _oracle/_owned above) a single instance only ever processes one run's deck
        and its trial clones -- cut()'s swap loop clones the deck every iteration
        (`DeckState.from_dict(deck.to_dict())`), so keying this by deck identity
        would recompute on every clone for no reason."""
        if not deck.price_cap_new_only or not deck.baseline_cards:
            return 0
        if self._baseline_lookup is None:
            lookup: dict[str, int] = {}
            for raw_name, qty in deck.baseline_cards.items():
                info = self._info(raw_name)
                key = (info["name"] if info else raw_name).lower()
                lookup[key] = lookup.get(key, 0) + qty
            self._baseline_lookup = lookup
        return self._baseline_lookup.get(canonical_name.lower(), 0)

    def _land_count_worsened(self, deck: DeckState, land_status: dict) -> bool:
        """True when THIS turn's own delta is what pushed the land count past the
        baseline it had at run start. should_fix_shape below intentionally skips
        auto-correcting land shape for improve/substitute -- a deck the user built
        land-heavy on purpose over prior turns shouldn't get its lands purged by an
        unrelated "swap this one card" request. But that protection has no business
        covering the case where the Architect's own delta, in this very turn, is
        what created or worsened the excess -- that's not a standing choice to
        respect, it's this turn's mistake to fix."""
        if not deck.baseline_cards:
            return False
        current_count = land_status.get("count")
        if current_count is None:
            return False
        baseline_count = 0
        for name, qty in deck.baseline_cards.items():
            info = self._info(name)
            if info and "land" in (info.get("type_line") or "").lower():
                baseline_count += qty
        return current_count > baseline_count

    def _legality_reason(self, deck: DeckState, name: str, new_qty: int) -> str | None:
        """Hard-rule reasons a card cannot occupy `new_qty` copies in this deck."""
        info = self._info(name)
        if not info:
            return "unknown card"
        if info["name"].lower() == (deck.commander or "").lower():
            return "commander"
        identity = set(deck.identity or [])
        if not set(info["color_identity"]).issubset(identity):
            return "color identity"
        status = commander_format_status(info)
        if status in ILLEGAL_COMMANDER_STATUS:
            return f"format {status}"
        if new_qty > 1 and not is_basic_land(info["type_line"]) and not allows_any_number(
            info["oracle_text"]
        ):
            return "singleton"
        owned_qty = self._owned_qty(info["name"])
        if deck.owned_only and owned_qty < new_qty:
            return "not owned"
        pool_norm = self._pool_norm(deck)
        if pool_norm is not None and _normalize_key(info["name"]) not in pool_norm:
            return "not in pool"
        unit = card_unit_price(info, deck.currency)
        current = 0
        key = deck._key(info["name"]) or deck._key(name)
        if key:
            current = deck.cards[key]
        add_qty = max(new_qty - current, 0)
        cost, _owned_used, buy_qty, unknown = acquisition_cost(
            add_qty, max(owned_qty - current, 0), unit, deck.owned_cost_zero
        )
        if deck.max_card_price is not None:
            # `current` above is the *live* deck count, which is the wrong baseline
            # for "is this new": a card already committed (e.g. strip_illegal
            # checking a card the Architect just substituted in) has current ==
            # new_qty, so add_qty would always be 0 and silently skip this gate.
            # Gate against the pre-run baseline instead, so a freshly substituted
            # card can't hide behind its own placement.
            price_current = self._baseline_qty(deck, info["name"])
            price_add_qty = max(new_qty - price_current, 0)
            _, _, price_buy_qty, _ = acquisition_cost(
                price_add_qty, max(owned_qty - price_current, 0), unit, deck.owned_cost_zero
            )
            if price_buy_qty > 0:
                if unit is None:
                    return "unknown price"
                if unit > deck.max_card_price:
                    return "over P_max"
        if deck.budget_cap is not None and add_qty > 0:
            if unknown:
                return "unknown price"
            used = (self._ctx or {}).get("budget")
            if used is None:
                used = enrich_deck(deck, self.db_path)["budget_used"]
            if cost is not None and used + cost > deck.budget_cap:
                return "over budget"
        return None

    def strip_illegal(self, deck: DeckState) -> dict:
        """Drop committed cards that break identity, format, singleton, or owned/price rules."""
        if deck.commander and not deck.identity:
            cmd = self._info(deck.commander)
            if cmd:
                deck.identity = list(cmd["color_identity"])
        removed = []
        for name, qty in list(deck.card_list().items()):
            reason = self._legality_reason(deck, name, qty)
            if not reason:
                continue
            deck.remove_card(name, qty)
            removed.append({"name": name, "quantity": qty, "reason": reason})
        if removed:
            print(f"[Solver] stripped {len(removed)} illegal cards: "
                  + ", ".join(f"{x['name']} ({x['reason']})" for x in removed[:8]))
        return {"removed": removed}

    def _seed_pool_from_retrieve(self, deck: DeckState, query: str) -> int:
        added = 0
        # Cards the Architect just cut this same turn don't get re-seeded -- see
        # _freshly_removed_names. Without this, seeding from the full card_pool
        # allowlist (or a broad retrieval query) hands them straight back to
        # fill() as if nothing happened.
        removed_now = self._freshly_removed_names(deck)
        # Pool-restricted decks skip vector retrieval entirely and seed straight
        # from the allowlist -- guarantees every pool card is a swap/fill
        # candidate, instead of depending on embedding relevance to rediscover
        # them (the pool is already small and fixed, there's nothing to search for).
        if deck.pool_only:
            for name, qty in deck.card_pool.items():
                if deck._key(name) or name.lower() in removed_now:
                    continue
                before = deck.pool_count()
                deck.add_to_pool(name, qty)
                if deck.pool_count() > before:
                    added += 1
        else:
            for name in self._retrieve(deck, query):
                if deck._key(name) or name.lower() in removed_now:
                    continue
                before = deck.pool_count()
                deck.add_to_pool(name, 1)
                if deck.pool_count() > before:
                    added += 1
        print(f"[Solver] seeded candidate_pool with {added} {'pool' if deck.pool_only else 'retrieved'} cards (pool={deck.pool_count()})")
        return added

    def solve(self, deck: DeckState, query: str = "", fill_to_99: bool = False) -> dict:
        """Strip illegal 99 cards, retrieve extras into the pool, fill holes, then cut."""
        stripped = self.strip_illegal(deck)
        n_stripped = sum(item["quantity"] for item in stripped["removed"])

        if deck.commander and deck.intent == "build":
            self._seed_catalog_filters(deck)

        land_status = {}
        if deck.commander:
            self._rebuild_context(deck, query)
            land_status = (self._ctx.get("mana") or {}).get("land_alert") or {}
        # Only auto-cut on a shape complaint when the user's own intent is to build/fix the
        # deck. A targeted "swap this card" on an intentionally land-heavy list (Azusa,
        # Lord Windgrace, extra-land-drop shells) must not trigger a land purge nobody asked
        # for; the quota is also a flat heuristic that doesn't know those archetypes exist.
        should_fix_shape = land_status.get("severity") in ("moderate", "severe") and (
            deck.intent in ("build", "cut") or self._land_count_worsened(deck, land_status)
        )
        # baseline_cards is the slot count this run started with (see DeckState.baseline_cards).
        # An architect turn that removes more than it adds (unequal delta.add/delta.remove,
        # instead of matched substitute pairs) can silently shrink an already-complete deck --
        # nothing else in this function catches that for intent="improve"/"substitute" since
        # should_fix_shape is gated to build/cut and n_stripped is 0 (nothing was illegal, it
        # was just removed on purpose). Only "cut" is exempt: there, shrinking is the ask.
        baseline_slot_count = sum(max(int(q), 0) for q in deck.baseline_cards.values())
        unintended_shrink = (
            baseline_slot_count == MAIN_DECK_SIZE
            and deck.slot_count() < MAIN_DECK_SIZE
            and deck.intent != "cut"
        )

        should_retrieve = bool(deck.commander) and (
            fill_to_99 or n_stripped > 0 or should_fix_shape or unintended_shrink
        )
        if should_retrieve:
            self._seed_pool_from_retrieve(deck, query)

        fill_report = None
        # Mirrors should_retrieve: retrieving into the pool for a reason and then not
        # spending it down leaves a seeded-but-unused pool and a deck that stays short
        # (a real incident: a "build" request landed with only 12/99 cards because the
        # pool got seeded here but fill() was never triggered to consume it).
        need_fill = bool(deck.commander) and deck.remaining_slots() > 0 and (
            fill_to_99
            or n_stripped > 0
            or should_fix_shape
            or unintended_shrink
            or (deck.intent == "cut" and bool(deck.candidate_pool))
        )
        if need_fill:
            deficit = deck.remaining_slots() if unintended_shrink else 0
            max_adds = None if (fill_to_99 or should_fix_shape) else max(n_stripped, deficit)
            print(f"[Solver] Filling to {'99' if fill_to_99 or should_fix_shape else 'replace stripped/shrink'} "
                  f"(max_adds={max_adds}, slots_left={deck.remaining_slots()})...")
            fill_report = self.fill(
                deck,
                query=query,
                retrieve=False,
                max_adds=max_adds,
                complete_fallback=bool(should_fix_shape and deck.intent == "build"),
            )
            if fill_to_99 and deck.remaining_slots() > 0:
                extra = self.fill(deck, query=query, retrieve=True)
                fill_report = {
                    "ok": extra.get("ok", True),
                    "added": (fill_report.get("added") or []) + (extra.get("added") or []),
                    "skipped": extra.get("skipped") or fill_report.get("skipped"),
                    "slot_count": extra.get("slot_count", deck.slot_count()),
                    "remaining_slots": extra.get("remaining_slots", deck.remaining_slots()),
                    "pool_count": extra.get("pool_count", deck.pool_count()),
                }

        cut_report = None
        if deck.slot_count() == MAIN_DECK_SIZE and deck.candidate_pool:
            # A severe land excess needs many 1-for-1 swaps in one pass, or it just
            # limps toward correct over several architect round-trips instead of one.
            max_swaps = 12
            if should_fix_shape:
                max_swaps = max(max_swaps, min(40, int(land_status.get("delta") or 0) + 4))
            print(f"[Solver] Cutting / swapping from candidate_pool (max_swaps={max_swaps})...")
            cut_report = self.cut(deck, query=query, max_swaps=max_swaps)

        if deck.commander and (fill_report or cut_report):
            # Re-read land count after fill/cut actually changed the deck, not before —
            # mirrors how a human checks land count only once the deck is settled, not
            # off the pre-fill snapshot that triggered the fix in the first place.
            self._rebuild_context(deck, query)
            land_status = (self._ctx.get("mana") or {}).get("land_alert") or {}

        return {"stripped": stripped, "fill": fill_report, "cut": cut_report, "land_alert": land_status}

    def can_add(self, deck: DeckState, name: str, quantity: int = 1) -> tuple[bool, str]:
        if deck.remaining_slots() < quantity:
            return False, "no remaining slots"
        info = self._info(name)
        if not info:
            return False, "unknown card"
        current = 0
        key = deck._key(info["name"]) or deck._key(name)
        if key:
            current = deck.cards[key]
        reason = self._legality_reason(deck, name, current + quantity)
        if reason:
            return False, reason
        return True, "ok"

    def warm_embeddings(self, names: list[str | None]):
        """Public entrypoint for callers outside fill()/cut() (e.g. the Analysis
        tab's score_breakdown() calls) that need geometry synergy without
        running a full solve pass."""
        self._warm_embeddings(names)

    def _warm_embeddings(self, names: list[str | None]):
        """Batch-load Chroma vectors for commander + candidates (id → vector)."""
        ids = []
        seen = set()
        for name in names:
            info = self._info(name) if name else None
            card_id = (info or {}).get("id")
            if card_id and card_id not in self._emb and card_id not in seen:
                seen.add(card_id)
                ids.append(card_id)
        if not ids:
            return
        if self.searcher is None:
            return
        for i in range(0, len(ids), 200):
            chunk = ids[i : i + 200]
            try:
                got = self.searcher.collection.get(ids=chunk, include=["embeddings"])
            except Exception:
                return
            ids_got = got.get("ids")
            embs = got.get("embeddings")
            if ids_got is None or embs is None:
                continue
            for card_id, vec in zip(ids_got, embs):
                if vec is not None:
                    self._emb[card_id] = vec

    def _ensure_view_store(self) -> dict | None:
        if self._view_store is False:
            self._view_store = load_card_views()
        return self._view_store or None

    def _view_pair(self, card_id: str | None) -> dict | None:
        if not card_id:
            return None
        if card_id in self._views:
            return self._views[card_id]
        store = self._ensure_view_store()
        if not store:
            return None
        idx = store["index"].get(card_id)
        if idx is None:
            return None
        return {"oracle": store["oracle"][idx], "type": store["type"][idx]} | {
            key: store[key][idx] for key in ("keywords", "mana") if key in store
        }

    def _geometry_cos(self, info: dict | None) -> float | None:
        cmd = (self._ctx or {}).get("cmd") or {}
        cmd_id = cmd.get("id") or ""
        card_id = (info or {}).get("id") or ""
        cv, tv = self._view_pair(cmd_id), self._view_pair(card_id)
        if cv and tv:
            return multi_view_cosine(cv, tv)
        a = self._emb.get(cmd_id)
        b = self._emb.get(card_id)
        if a is None or b is None:
            return None
        return cosine(a, b)

    def _view_cosines(self, info: dict | None) -> dict[str, float | None]:
        cmd = (self._ctx or {}).get("cmd") or {}
        cv = self._view_pair(cmd.get("id") or "")
        tv = self._view_pair((info or {}).get("id") or "")
        if not cv or not tv:
            return {"oracle": None, "type": None, "keywords": None, "mana": None}
        out = {}
        for key in ("oracle", "type", "keywords", "mana"):
            out[key] = cosine(cv[key], tv[key]) if key in cv and key in tv else None
        return out

    def _card_roles(self, info: dict) -> set[str]:
        arch = (self._ctx or {}).get("archetype") or "generic"
        return planned_roles(
            info.get("type_line") or "",
            info.get("oracle_text") or "",
            info.get("keywords"),
            arch,
        )

    def _token_adj(self, info: dict) -> float:
        cmd = (self._ctx or {}).get("cmd") or {}
        cmd_cls = token_classes(cmd.get("type_line") or "", cmd.get("oracle_text") or "")
        card_cls = token_classes(info.get("type_line") or "", info.get("oracle_text") or "")
        if "token_producer" in cmd_cls and "token_payoff" in card_cls:
            return TOKEN_ALIGN_BONUS
        if "token_payoff" in cmd_cls and "token_producer" in card_cls:
            return TOKEN_ALIGN_BONUS
        return 0.0

    def _theme_match(self, info: dict) -> bool:
        """True if the card shares/names a creature type the commander is actually
        built around (self_referential_types), or if the commander isn't a tribal
        commander at all -- being a certain creature type isn't the same as
        synergizing with it (see self_referential_types)."""
        cmd = (self._ctx or {}).get("cmd") or {}
        tribal_types = self_referential_types(cmd)
        if not tribal_types:
            return True
        blob = f"{info.get('name','')} {info.get('type_line','')} {info.get('oracle_text','')}".lower()
        card_types = creature_types(info.get("type_line") or "")
        return bool(tribal_types & card_types) or any(t in blob for t in tribal_types)

    def _tribe_adj(self, info: dict) -> float:
        profile = (self._ctx or {}).get("profile") or profile_for("generic")
        if not profile.enforce_tribe:
            return 0.0
        cmd = (self._ctx or {}).get("cmd") or {}
        if not self_referential_types(cmd):
            return 0.0
        if self._theme_match(info):
            return TRIBE_MATCH_BONUS if is_creature_card(info.get("type_line") or "") else 0.0
        if is_creature_card(info.get("type_line") or ""):
            return TRIBE_MISS_PENALTY
        return 0.0

    def _skip_reason(self, info: dict, deck: DeckState | None = None) -> str | None:
        if self._off_tribe_creature(info):
            return "off-tribe creature"
        if self._dead_land(info):
            return "land produces no mana"
        if deck is None:
            return None
        tl = info.get("type_line") or ""
        if deck.land_types_strict:
            if "land" in tl.lower() and not is_basic_land(tl):
                if not self._matches_preferred_land(info, deck):
                    return "non-preferred land (land_types_strict)"
        if "land" in tl.lower():
            land_high = int(ROLE_QUOTAS["land"][1])
            allow_complete_basic = deck.require_complete and is_basic_land(tl)
            if self._land_count(deck) >= land_high and not allow_complete_basic:
                return "land quota full"
        if deck.theme_types and self._matches_theme_type(info, deck):
            if self._theme_count(deck) >= THEME_HARD_CAP:
                return "theme type cap"
        return None

    def _off_tribe_creature(self, info: dict) -> bool:
        profile = (self._ctx or {}).get("profile") or profile_for("generic")
        if not profile.enforce_tribe:
            return False
        cmd = (self._ctx or {}).get("cmd") or {}
        if not self_referential_types(cmd):
            return False
        return is_creature_card(info.get("type_line") or "") and not self._theme_match(info)

    def _dead_land(self, info: dict) -> bool:
        """Utility land that never taps for mana — not a land-slot fill."""
        if "land" not in (info.get("type_line") or "").lower():
            return False
        return not produces_mana(
            info.get("type_line") or "",
            info.get("oracle_text") or "",
            info.get("mana_cost") or "",
        )

    def _score_parts(
        self,
        deck: DeckState,
        name: str,
        query: str = "",
        skip_self: bool = False,
    ) -> dict:
        info = self._info(name)
        if not info:
            return {"error": "unknown card", "total": -999.0, "name": name}
        if self._ctx is None:
            self._rebuild_context(deck, query)
        ctx = self._ctx
        target = ctx["target"]
        card_tok = _tokens(f"{info['name']} {info['type_line']} {info['oracle_text']}")
        jaccard = _jaccard(target, card_tok)
        distance = info.get("_distance")
        chroma_synergy = None
        if distance is not None:
            chroma_synergy = 1.0 / (1.0 + float(distance))
        theme = self._theme_match(info)
        geometry = self._geometry_cos(info)
        geo_views = self._view_cosines(info)
        geo_oracle, geo_type = geo_views.get("oracle"), geo_views.get("type")
        # Retrieval distance is recall. Synergy is commander↔card cosine when
        # the index is loaded; otherwise Jaccard. Chroma query-distance may
        # rerank only after a tribe/text gate (name-query false friends).
        if geometry is not None:
            synergy = geometry
        else:
            synergy = jaccard
            land = "land" in (info.get("type_line") or "").lower()
            if (
                chroma_synergy is not None
                and not land
                and (theme or jaccard >= CHROMA_JACCARD_GATE)
            ):
                synergy = max(jaccard, chroma_synergy)

        roles = self._card_roles(info)
        quotas = ctx["quotas"] if ctx.get("quotas") else quotas_for(ctx.get("archetype"))
        role_bonuses = {
            role: role_need_bonus(role, ctx["counts"], quotas) for role in sorted(roles)
        }
        others = [role_bonuses[r] for r in role_bonuses if r != "land"]
        role_score = max(others, default=0.0) if others else 0.0
        if "land" in role_bonuses:
            role_score += role_bonuses["land"]
        tribe = self._tribe_adj(info)
        token_align = self._token_adj(info)

        redundancy = 0.0
        redundancy_with = None
        for other, other_tok in zip(ctx["deck_cards"], ctx["other_toks"]):
            other_name = other.get("name") or ""
            if skip_self and other_name.lower() == info["name"].lower():
                continue
            overlap = _jaccard(card_tok, other_tok)
            if roles & self._card_roles(other):
                overlap *= 1.25
            if overlap > redundancy:
                redundancy = overlap
                redundancy_with = other_name

        shape = shape_bonus(info, ctx.get("mana"), deck.identity)
        curve_penalty = shape["curve_penalty"]
        land_bonus = float(shape["land_bonus"] or 0.0)
        pref_land = 0.0
        theme_bonus = 0.0
        land_urgent = 0.0
        is_land = "land" in (info.get("type_line") or "").lower()
        if deck.preferred_land_types and self._matches_preferred_land(info, deck):
            pref_land = PREFERRED_LAND_BONUS
            land_bonus += pref_land
        if (
            deck.theme_types
            and self._matches_theme_type(info, deck)
            and self._theme_count(deck) < THEME_SOFT_CAP
        ):
            theme_bonus = THEME_TYPE_BONUS
        facts = classify_card(
            info.get("name") or name,
            info.get("type_line") or "",
            info.get("oracle_text") or "",
            info.get("keywords"),
        )
        requested = set(requirement_families(query))
        symbolic_bonus = sum(
            0.45
            for family, enabled in (
                ("counter", facts.is_counter),
                ("removal", facts.is_removal),
                ("draw", facts.is_draw),
                ("ramp", facts.is_ramp),
                ("protection", facts.is_protection),
                ("extra_combat", facts.is_extra_combat),
                ("evasion", facts.is_evasion),
                ("tutor", facts.is_tutor),
                ("token_engine", facts.is_token_engine),
                ("graveyard", facts.is_graveyard),
                ("sacrifice", facts.is_sacrifice),
            )
            if family in requested and enabled
        )
        # When under land quota, lands must beat glue or 5c builds starve on mana.
        if is_land and self._land_count(deck) < int(ROLE_QUOTAS["land"][0]):
            land_urgent = 3.0
            if deck.preferred_land_types and self._matches_preferred_land(info, deck):
                land_urgent += 0.5

        unit = card_unit_price(info, deck.currency) or 0.0
        value = synergy / (unit + 0.5) if deck.budget_cap is not None else 0.0
        total = (
            2.0 * synergy
            + role_score
            + tribe
            + token_align
            - 1.4 * redundancy
            + value
            + shape["total"]
            + pref_land
            + theme_bonus
            + land_urgent
            + symbolic_bonus
        )
        profile = ctx.get("profile") or profile_for(ctx.get("archetype"))
        cap = profile.max_creatures
        if cap is not None and is_creature_card(info.get("type_line") or ""):
            bodies = sum(
                int(c.get("quantity") or 1)
                for c in ctx["deck_cards"]
                if is_creature_card(c.get("type_line") or "")
            )
            if skip_self and any(
                (c.get("name") or "").lower() == info["name"].lower() for c in ctx["deck_cards"]
            ):
                bodies = max(0, bodies - 1)
            if bodies >= cap:
                total -= 2.2
            elif bodies >= int(cap * 0.7):
                total -= 0.8
        return {
            "name": info["name"],
            "type_line": info.get("type_line") or "",
            "oracle_text": info.get("oracle_text") or "",
            "cmc": info.get("cmc") or 0,
            "mana_cost": info.get("mana_cost") or "",
            "price_usd": info.get("price_usd"),
            "roles": sorted(roles),
            "shared_tokens": sorted(target & card_tok),
            "jaccard": jaccard,
            "chroma_distance": None if distance is None else float(distance),
            "chroma_query": info.get("_distance_query"),
            "chroma_synergy": chroma_synergy,
            "geometry": geometry,
            "geometry_oracle": geo_oracle,
            "geometry_type": geo_type,
            "geometry_keywords": geo_views.get("keywords"),
            "geometry_mana": geo_views.get("mana"),
            "theme_match": theme,
            "synergy": synergy,
            "role_bonuses": role_bonuses,
            "role_score": role_score,
            "tribe": tribe,
            "token_align": token_align,
            "redundancy": redundancy,
            "redundancy_with": redundancy_with,
            "curve_penalty": curve_penalty,
            "curve_bonus": shape["curve_bonus"],
            "land_bonus": land_bonus,
            "mana_bonus": shape["mana_bonus"],
            "shape": shape["total"],
            "value": value,
            "symbolic_facts": facts.to_dict(),
            "symbolic_bonus": symbolic_bonus,
            "total": total,
            "info": info,
        }

    def score_candidate(self, deck: DeckState, name: str, query: str = "") -> float:
        parts = self._score_parts(deck, name, query, skip_self=False)
        if parts.get("error"):
            return -999.0
        return parts["total"]

    def score_breakdown(
        self,
        deck: DeckState,
        name: str,
        query: str = "",
        skip_self: bool = True,
    ) -> dict:
        """Decompose score_candidate. skip_self avoids Jaccard=1 when the card is already in the 99."""
        parts = self._score_parts(deck, name, query, skip_self=skip_self)
        if parts.get("error"):
            # Same key set as the success return below (zeroed/empty), so a
            # caller that renders every field (e.g. the Analysis tab) doesn't
            # need a second, error-only shape to special-case. "error" itself
            # stays absent from the success dict -- tools.py's
            # score_card_json checks `"error" not in breakdown` to set "ok".
            return {
                "name": name,
                "type_line": "",
                "oracle_text": "",
                "cmc": 0,
                "mana_cost": "",
                "price_usd": None,
                "roles": [],
                "shared_tokens": [],
                "jaccard": 0.0,
                "chroma_distance": None,
                "chroma_query": None,
                "chroma_synergy": None,
                "geometry": None,
                "geometry_oracle": None,
                "geometry_type": None,
                "geometry_keywords": None,
                "geometry_mana": None,
                "theme_match": False,
                "synergy": 0.0,
                "role_bonuses": {},
                "role_score": 0.0,
                "tribe": 0.0,
                "token_align": 0.0,
                "redundancy": 0.0,
                "redundancy_with": None,
                "curve_penalty": 0.0,
                "curve_bonus": 0.0,
                "land_bonus": 0.0,
                "mana_bonus": 0.0,
                "shape": 0.0,
                "value": 0.0,
                "symbolic_facts": {},
                "symbolic_bonus": 0.0,
                "total": -999.0,
                "error": parts["error"],
            }
        return {
            "name": parts["name"],
            "type_line": parts["type_line"],
            "oracle_text": parts["oracle_text"],
            "cmc": parts["cmc"],
            "mana_cost": parts["mana_cost"],
            "price_usd": parts["price_usd"],
            "roles": parts["roles"],
            "shared_tokens": parts["shared_tokens"],
            "jaccard": round(parts["jaccard"], 4),
            "chroma_distance": None
            if parts["chroma_distance"] is None
            else round(parts["chroma_distance"], 4),
            "chroma_query": parts["chroma_query"],
            "chroma_synergy": None
            if parts["chroma_synergy"] is None
            else round(parts["chroma_synergy"], 4),
            "geometry": None
            if parts.get("geometry") is None
            else round(parts["geometry"], 4),
            "geometry_oracle": None
            if parts.get("geometry_oracle") is None
            else round(parts["geometry_oracle"], 4),
            "geometry_type": None
            if parts.get("geometry_type") is None
            else round(parts["geometry_type"], 4),
            "geometry_keywords": None
            if parts.get("geometry_keywords") is None
            else round(parts["geometry_keywords"], 4),
            "geometry_mana": None
            if parts.get("geometry_mana") is None
            else round(parts["geometry_mana"], 4),
            "theme_match": parts["theme_match"],
            "synergy": round(parts["synergy"], 4),
            "role_bonuses": parts["role_bonuses"],
            "role_score": round(parts["role_score"], 4),
            "tribe": round(parts["tribe"], 4),
            "token_align": round(parts.get("token_align") or 0.0, 4),
            "redundancy": round(parts["redundancy"], 4),
            "redundancy_with": parts["redundancy_with"],
            "curve_penalty": parts["curve_penalty"],
            "curve_bonus": round(parts.get("curve_bonus") or 0.0, 4),
            "land_bonus": round(parts.get("land_bonus") or 0.0, 4),
            "mana_bonus": round(parts.get("mana_bonus") or 0.0, 4),
            "shape": round(parts.get("shape") or 0.0, 4),
            "value": round(parts["value"], 4),
            "symbolic_facts": parts.get("symbolic_facts") or {},
            "symbolic_bonus": round(parts.get("symbolic_bonus") or 0.0, 4),
            "total": round(parts["total"], 4),
        }

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

    def _freshly_removed_names(self, deck: DeckState) -> set[str]:
        """Mirror of _freshly_touched_names, in the opposite direction: cards the
        Architect just took OUT of deck.cards this round (via last_delta), which
        _seed_pool_from_retrieve must not hand straight back to fill() -- a real
        incident: pool_only + a shrink-refill (see unintended_shrink in solve())
        re-seeds candidate_pool from the full card_pool allowlist, which still
        contains every card the Architect just deliberately cut, and fill()'s
        scorer has no notion that they were just rejected on purpose -- it just
        sees legal, decently-scoring cards and adds them right back, silently
        undoing the removal the Architect (and the user) asked for."""
        delta = deck.last_delta or {}
        names = {
            str(item.get("name")).lower()
            for item in delta.get("removed") or []
            if item.get("name")
        }
        names.update(
            str(item.get("out")).lower()
            for item in delta.get("substituted") or []
            if item.get("out")
        )
        return names

    def _freshly_touched_names(self, deck: DeckState) -> set[str]:
        """Cards the Architect placed into deck.cards this round (via last_delta) --
        protected from cut()'s same-pass pool sweep, so a deliberate addition/
        substitution can't be undone in the very round it was made by a scoring
        pass that has no notion of what was just deliberately chosen."""
        delta = deck.last_delta or {}
        names = set()
        for item in delta.get("added") or []:
            name = item.get("name")
            if name:
                names.add(str(name).lower())
        for item in delta.get("substituted") or []:
            name = item.get("in")
            if name:
                names.add(str(name).lower())
        return names

    def _worst_cut(
        self,
        deck: DeckState,
        query: str,
        prefer_expensive: bool,
        protected_names: set[str] | None = None,
    ) -> str | None:
        if self._ctx is None:
            self._rebuild_context(deck, query)
        deck_cards = self._ctx["deck_cards"]
        counts = self._ctx["counts"]
        quotas = self._ctx.get("quotas") or quotas_for(self._ctx.get("archetype"))
        profile = self._ctx.get("profile") or profile_for("generic")
        land_count = counts.get("land", 0)
        worst_name = None
        worst_score = None
        for card in deck_cards:
            name = card["name"]
            if protected_names and name.lower() in protected_names:
                continue
            roles = self._card_roles(card)
            if "land" in roles and land_count <= quotas["land"][0]:
                continue
            protected = False
            for role in roles:
                if role in quotas and counts.get(role, 0) <= quotas[role][0]:
                    protected = True
                    break
            for role in profile.protect_roles:
                if role in roles and counts.get(role, 0) <= (quotas.get(role) or (0, 0))[1]:
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
            info = self._info(name) or {}
            if self._skip_reason(info, deck):
                continue
            score = self.score_candidate(deck, name, query)
            if best_score is None or score > best_score:
                best_score = score
                best_name = name
        return best_name

    def _best_basic(self, deck: DeckState, allow_overquota: bool = False) -> str | None:
        # Never suggest basics (or preferred lands-as-basics) once land quota is met.
        if self._land_count(deck) >= int(ROLE_QUOTAS["land"][1]) and not allow_overquota:
            return None
        # Prefer preferred-type lands still in the pool when land slots are short.
        if deck.preferred_land_types and (
            self._land_count(deck) < int(ROLE_QUOTAS["land"][1]) or not allow_overquota
        ):
            for name in list(deck.candidate_pool):
                info = self._info(name) or {}
                if not self._matches_preferred_land(info, deck):
                    continue
                if self._skip_reason(info, deck):
                    continue
                ok, _reason = self.can_add(deck, name)
                if ok:
                    return info.get("name") or name

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
                searcher = RAGSearcher.shared()
                self.searcher = searcher
        except Exception as exc:
            print(f"[Solver] retrieval unavailable ({type(exc).__name__}: {exc})")
            return []
        cmd = self._info(deck.commander) if deck.commander else None
        arch = self._plan(deck, query)
        profile = profile_for(arch)
        queries = [q for q in [query, (cmd or {}).get("oracle_text")] if q]
        if profile.retrieve_tribe and cmd:
            tribal_types = self_referential_types(cmd)
            if tribal_types:
                queries.append(" ".join(sorted(tribal_types)) + " creature")
        if cmd:
            for theme in detect_known_themes(cmd):
                queries.extend(THEME_QUERIES[theme])
        queries.extend(search_queries_for(arch))
        colors = list(deck.identity or (cmd or {}).get("color_identity") or [])
        found = []
        # Cap sized for the worst case: 2 base + 1 tribal + theme queries
        # + archetype search queries. Left slack to 16 for headroom.
        for q in queries[:16]:
            try:
                hits = searcher.search_cards(
                    query=q,
                    allowed_colors=colors,
                    owned_only=deck.owned_only,
                    card_pool=self._pool_norm(deck),
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
                        info["_distance_query"] = q
                found.append(hit["name"])
        print(f"[Solver] retrieved {len(found)} hits ({len(set(n.lower() for n in found))} unique)")
        return found
