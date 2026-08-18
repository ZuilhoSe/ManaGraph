import sqlite3
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_DIR, exist_ok=True)

DB_NAME = os.path.join(DATA_DIR, "managraph.db")


class CommanderValidator:
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME)
        self.cursor = self.conn.cursor()

    def get_card_info(self, card_name):
        """Fetch color identity, oracle text, and type line from the Oracle catalog."""
        self.cursor.execute("""
            SELECT color_identity, oracle_text, type_line
            FROM cards
            WHERE name = ?
        """, (card_name,))

        row = self.cursor.fetchone()
        if row:
            return {
                "color_identity": json.loads(row[0]),  # e.g. ['R', 'B']
                "oracle_text": row[1] if row[1] else "",
                "type_line": row[2] if row[2] else ""
            }
        return None

    def validate_deck(self, commander_name, deck_list):
        """
        Validate a commander plus a name -> quantity map.
        Example: {"Sol Ring": 1, "Mountain": 10, "Relentless Rats": 4}
        Returns a report of rule violations.
        """
        cmd_info = self.get_card_info(commander_name)
        if not cmd_info:
            return {"error": f"Commander '{commander_name}' was not found in the Oracle catalog."}

        cmd_identity = set(cmd_info["color_identity"])

        singleton_violations = []
        color_violations = []
        cards_not_found = []

        for card_name, qty in deck_list.items():
            card_info = self.get_card_info(card_name)

            if not card_info:
                cards_not_found.append(card_name)
                continue

            # Color identity: the card's identity must be a subset of the commander's.
            # Colorless cards (empty identity) always pass.
            card_identity = set(card_info["color_identity"])
            if not card_identity.issubset(cmd_identity):
                color_violations.append(
                    f"{card_name} (colors: {list(card_identity)} | allowed: {list(cmd_identity)})"
                )

            # Singleton: at most one copy unless it is a basic land or an "any number" card.
            if qty > 1:
                if "Basic Land" in card_info["type_line"] or "Basic Snow Land" in card_info["type_line"]:
                    continue

                if "A deck can have any number of cards named" in card_info["oracle_text"]:
                    continue

                singleton_violations.append(f"{card_name} ({qty} copies)")

        is_valid = len(singleton_violations) == 0 and len(color_violations) == 0

        return {
            "valid": is_valid,
            "commander": commander_name,
            "commander_identity": list(cmd_identity),
            "color_errors": color_violations,
            "singleton_errors": singleton_violations,
            "unknown_cards": cards_not_found
        }

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    print("Validating Commander rules against inventory data...\n")

    validator = CommanderValidator()

    commander = "Krenko, Mob Boss"
    deck_location = "deck_krenko"

    validator.cursor.execute("""
        SELECT card_name, total_quantity, allocations
        FROM inventory
    """)

    deck = {}
    for row in validator.cursor.fetchall():
        card_name = row[0]
        allocations = json.loads(row[2])

        if deck_location in allocations:
            deck[card_name] = allocations[deck_location]

    if not deck:
        print(f"No cards found in location '{deck_location}'.")
        print("Run import_inventory.py first to load the test deck list.")
    else:
        report = validator.validate_deck(commander, deck)

        print(f"Commander: {report['commander']} (colors: {report['commander_identity']})")
        print(f"Legal deck? {'YES' if report['valid'] else 'NO'}")

        if not report["valid"]:
            print("\n--- PROBLEMS FOUND ---")
            if report["color_errors"]:
                print("Color identity violations:")
                for error in report["color_errors"]:
                    print(f"  - {error}")

            if report["singleton_errors"]:
                print("\nSingleton violations (max 1):")
                for error in report["singleton_errors"]:
                    print(f"  - {error}")

            if report["unknown_cards"]:
                print("\nCards not found in the Oracle catalog:")
                for card in report["unknown_cards"]:
                    print(f"  - {card}")

    validator.close()
