"""Inventory listing for the web UI's Collection tab.

Only reads via catalog/inventory — never edits the core agent modules.
"""

from catalog import get_oracle_card
from inventory import list_inventory


def list_inventory_cards() -> list[dict]:
    items = []
    for entry in list_inventory():
        card = get_oracle_card(entry["card_name"]) or {}
        items.append(
            {
                "name": entry["card_name"],
                "quantity": entry["total_quantity"],
                "allocations": entry["allocations"],
                "type_line": card.get("type_line", ""),
                "mana_cost": card.get("mana_cost", ""),
                "cmc": card.get("cmc"),
                "price_usd": card.get("price_usd"),
                "price_eur": card.get("price_eur"),
            }
        )
    return items
