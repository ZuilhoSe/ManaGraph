import json
import os
import sqlite3

from catalog import _row_to_card, card_unit_price, ensure_schema, enrich_deck, get_oracle_card
from deck_state import MAIN_DECK_SIZE, DeckState, _normalize_key
from inventory import get_card as get_inventory_card
from mana import diagnose_deck, strategy_from_name

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_NAME = os.path.join(DATA_DIR, "managraph.db")

ILLEGAL_COMMANDER_STATUS = {"not_legal", "banned", "restricted"}


def is_basic_land(type_line: str) -> bool:
    return "Basic Land" in (type_line or "") or "Basic Snow Land" in (type_line or "")


def allows_any_number(oracle_text: str) -> bool:
    return "A deck can have any number of cards named" in (oracle_text or "")


def is_legal_commander(info: dict) -> bool:
    type_line = info.get("type_line") or ""
    oracle = (info.get("oracle_text") or "").lower()
    if "Legendary" in type_line and "Creature" in type_line:
        return True
    return "can be your commander" in oracle


def commander_format_status(info: dict) -> str:
    legalities = info.get("legalities") or {}
    return legalities.get("commander") or "not_legal"


def legal_commanders_in_pool(card_pool: dict, db_path: str = DB_NAME) -> list[str]:
    """Every card_pool entry that could legally serve as commander -- not just
    the source decks' own commanders, any qualifying card physically in the
    pool. Shared by service/handlers/decks.build_pool (lets the web UI's
    commander picker offer only these) and main_agent.architect_node (hands
    the Architect a concrete answer space under pool_only instead of letting
    it pick from training data and get rejected downstream).

    One batched query instead of get_oracle_card() per pool card -- a pool
    can run ~200 cards and that pattern was a measured ~150ms/card elsewhere
    (see list_inventory_cards in service/handlers/inventory.py)."""
    if not card_pool:
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        placeholders = ",".join("?" for _ in card_pool)
        rows = conn.execute(
            f"SELECT * FROM cards WHERE name COLLATE NOCASE IN ({placeholders})",
            list(card_pool),
        ).fetchall()
    finally:
        conn.close()

    commanders = []
    for row in rows:
        info = _row_to_card(row)
        if is_legal_commander(info) and commander_format_status(info) not in ILLEGAL_COMMANDER_STATUS:
            commanders.append(info["name"])
    return sorted(commanders)


class CommanderValidator:
    def __init__(self, db_path: str = DB_NAME):
        self.db_path = db_path

    def get_card_info(self, card_name: str):
        return get_oracle_card(card_name, self.db_path)

    def validate_deck(self, commander_name, deck_list, **constraints):
        deck = DeckState.from_dict(
            {
                "commander": commander_name,
                "cards": deck_list or {},
                **constraints,
            }
        )
        return self.validate_deck_state(deck)

    def validate_deck_state(self, deck: DeckState | dict) -> dict:
        if isinstance(deck, dict):
            deck = DeckState.from_dict(deck)

        if not deck.commander:
            return {
                "error": "No commander was set.",
                "valid": False,
                "complete": False,
            }

        cmd_info = self.get_card_info(deck.commander)
        if not cmd_info:
            return {
                "error": f"Commander '{deck.commander}' was not found in the Oracle catalog.",
                "valid": False,
                "complete": False,
            }

        pool_norm = (
            {_normalize_key(n) for n in deck.card_pool} if deck.pool_only and deck.card_pool else None
        )

        commander_errors = []
        if not is_legal_commander(cmd_info):
            commander_errors.append(
                f"{cmd_info['name']} is not a legal commander (needs a legendary creature "
                "or 'can be your commander')."
            )
        cmd_status = commander_format_status(cmd_info)
        if cmd_status in ILLEGAL_COMMANDER_STATUS:
            commander_errors.append(
                f"{cmd_info['name']} is {cmd_status} in Commander."
            )
        if pool_norm is not None and _normalize_key(cmd_info["name"]) not in pool_norm:
            commander_errors.append(
                f"{cmd_info['name']} is not in the selected card pool."
            )

        identity = list(deck.identity) if deck.identity else list(cmd_info["color_identity"])
        cmd_identity = set(identity)
        enrichment = enrich_deck(deck, self.db_path)

        singleton_violations = []
        color_violations = []
        format_violations = []
        cards_not_found = []
        owned_violations = []
        pool_violations = []
        price_violations = []
        warnings = []

        # Route through the deck's own mana_strategy (static vs. hypergeometric) instead
        # of the bare, strategy-blind land_alert() -- otherwise this warning silently
        # reports against the flat 34-38 quota even when the user picked the
        # curve-aware hypergeometric target, disagreeing with what the Architect (which
        # diagnoses via this same strategy) and the Solver (_rebuild_context, same
        # strategy) already used to make their own decisions on this exact deck.
        mana_report = diagnose_deck(deck, self.db_path, strategy=strategy_from_name(deck.mana_strategy))
        alert = mana_report["land_alert"]
        if alert["severity"] in ("moderate", "severe"):
            direction = "too few" if alert["status"] == "low" else "too many"
            low, high = alert["quota"]
            warnings.append(
                f"Land count {alert['count']} is {direction} lands: off the {low}-{high} quota by "
                f"{alert['delta']} ({alert['pct'] * 100:.0f}%, {alert['severity']})."
            )

        for name, qty in deck.card_list().items():
            card_info = self.get_card_info(name)
            if not card_info:
                cards_not_found.append(name)
                continue

            card_identity = set(card_info["color_identity"])
            if not card_identity.issubset(cmd_identity):
                color_violations.append(
                    f"{card_info['name']} (colors: {sorted(card_identity)} | allowed: {sorted(cmd_identity)})"
                )

            if qty > 1 and not is_basic_land(card_info["type_line"]) and not allows_any_number(card_info["oracle_text"]):
                singleton_violations.append(f"{card_info['name']} ({qty} copies)")

            status = commander_format_status(card_info)
            if status in ILLEGAL_COMMANDER_STATUS:
                format_violations.append(f"{card_info['name']} is {status} in Commander")

            inv = get_inventory_card(name, self.db_path)
            owned_qty = inv["total_quantity"] if inv else 0
            if deck.owned_only and owned_qty < qty:
                owned_violations.append(
                    f"{card_info['name']} (need {qty}, own {owned_qty})"
                )

            if pool_norm is not None and _normalize_key(card_info["name"]) not in pool_norm:
                pool_violations.append(card_info["name"])

        slot_count = deck.slot_count()
        size_errors = []
        if slot_count > MAIN_DECK_SIZE:
            size_errors.append(
                f"Main deck has {slot_count} cards; Commander allows {MAIN_DECK_SIZE}."
            )
        if deck.require_complete and slot_count != MAIN_DECK_SIZE:
            size_errors.append(
                f"Main deck has {slot_count}/{MAIN_DECK_SIZE} cards (complete deck required)."
            )
        elif slot_count < MAIN_DECK_SIZE:
            warnings.append(f"Incomplete main deck: {slot_count}/{MAIN_DECK_SIZE} cards.")

        budget_used = enrichment["budget_used"]
        if deck.max_card_price is not None:
            # price_cap_new_only (default True): a card already sitting in the
            # decklist before this run started doesn't need to satisfy P_max --
            # the cap is a guard on what the agent is about to add/buy, not a
            # demand to rip out a pricey staple the user brought in themselves.
            # Resolved through the catalog (not a raw string match) so a
            # baseline card pasted as "Front/Back" still matches the canonical
            # "Front // Back" name enrich_deck reports.
            baseline_keys = set()
            if deck.price_cap_new_only:
                for name in deck.baseline_cards or {}:
                    info = self.get_card_info(name)
                    baseline_keys.add((info["name"] if info else name).lower())
            for detail in enrichment["details"]:
                if detail["is_commander"]:
                    continue
                if detail["buy_qty"] <= 0:
                    continue
                if detail["name"].lower() in baseline_keys:
                    continue
                unit = detail["unit_price"]
                if unit is None:
                    price_violations.append(
                        f"{detail['name']} has no snapshot price; cannot enforce P_max."
                    )
                elif unit > deck.max_card_price:
                    price_violations.append(
                        f"{detail['name']} costs {unit} {deck.currency} > P_max {deck.max_card_price}"
                    )

        if deck.budget_cap is not None:
            if enrichment["price_unknown"]:
                price_violations.append(
                    "Unknown snapshot prices: " + ", ".join(enrichment["price_unknown"])
                )
            if budget_used > deck.budget_cap:
                price_violations.append(
                    f"Deck spend {budget_used} {deck.currency} exceeds budget cap {deck.budget_cap}"
                )

        valid = not any(
            [
                commander_errors,
                color_violations,
                singleton_violations,
                format_violations,
                cards_not_found,
                owned_violations,
                pool_violations,
                size_errors,
                price_violations,
            ]
        )

        return {
            "valid": valid,
            "complete": slot_count == MAIN_DECK_SIZE and not commander_errors,
            "commander": cmd_info["name"],
            "commander_identity": sorted(cmd_identity),
            "slot_count": slot_count,
            "target_slots": MAIN_DECK_SIZE,
            "budget_used": budget_used,
            "budget_cap": deck.budget_cap,
            "max_card_price": deck.max_card_price,
            "currency": deck.currency,
            "curve": enrichment["curve"],
            "land_count": enrichment["land_count"],
            "land_alert": alert,
            "commander_errors": commander_errors,
            "color_errors": color_violations,
            "singleton_errors": singleton_violations,
            "format_errors": format_violations,
            "size_errors": size_errors,
            "owned_errors": owned_violations,
            "pool_errors": pool_violations,
            "price_errors": price_violations,
            "unknown_cards": cards_not_found,
            "warnings": warnings,
            "catalog_meta": enrichment["catalog_meta"],
        }

    def close(self):
        return None


if __name__ == "__main__":
    from inventory import list_inventory

    print("Validating Commander rules against inventory data...\n")

    validator = CommanderValidator()
    commander = "Krenko, Mob Boss"
    deck_location = "deck_krenko"
    deck_cards = {}
    for row in list_inventory(deck_location):
        deck_cards[row["card_name"]] = row["quantity"]

    if not deck_cards:
        print(f"No cards found in location '{deck_location}'.")
        print("Run import_inventory.py first to load the test deck list.")
    else:
        report = validator.validate_deck(commander, deck_cards)
        print(json.dumps(report, indent=2))
        print(f"\nCommander: {report.get('commander')} (colors: {report.get('commander_identity')})")
        print(f"Legal deck? {'YES' if report.get('valid') else 'NO'}")
        if report.get("warnings"):
            print("Warnings:", "; ".join(report["warnings"]))
