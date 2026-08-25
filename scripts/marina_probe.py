"""Probe Rooms, Gates, and enchantress pieces for Marina Vendrell."""
from __future__ import annotations

import json
import os
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from catalog import DB_NAME, get_oracle_card

WUBRG = {"W", "U", "B", "R", "G"}


def legal(ci_raw: str) -> bool:
    ci = set(json.loads(ci_raw or "[]"))
    return ci.issubset(WUBRG)


def main():
    cmd = get_oracle_card("Marina Vendrell")
    print("COMMANDER:", cmd["name"] if cmd else "MISSING")
    if cmd:
        print("  identity:", cmd["color_identity"])
        print("  type:", cmd["type_line"])
        print("  oracle:", (cmd.get("oracle_text") or "")[:280])

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    rooms = conn.execute(
        "SELECT name, type_line, mana_cost, cmc, color_identity, oracle_text "
        "FROM cards WHERE type_line LIKE '%Room%' ORDER BY cmc, name"
    ).fetchall()
    legal_rooms = [r for r in rooms if legal(r["color_identity"])]
    print(f"\nROOMS total={len(rooms)} legal_WUBRG={len(legal_rooms)}")
    for r in legal_rooms:
        ci = json.loads(r["color_identity"] or "[]")
        print(f"  [{r['cmc']}] {r['name']}  {r['mana_cost']}  {ci}")

    gates = conn.execute(
        "SELECT name, type_line, color_identity, oracle_text "
        "FROM cards WHERE type_line LIKE '%Gate%' OR type_line LIKE '% — Gate%' "
        "ORDER BY name"
    ).fetchall()
    legal_gates = [g for g in gates if legal(g["color_identity"])]
    print(f"\nGATES total={len(gates)} legal_WUBRG={len(legal_gates)}")
    for g in legal_gates:
        ci = json.loads(g["color_identity"] or "[]")
        print(f"  {g['name']}  {g['type_line']}  {ci}")

    # Classic enchantress draw engines + room payoffs
    seeds = [
        "Enchantress's Presence",
        "Argothian Enchantress",
        "Mesa Enchantress",
        "Verduran Enchantress",
        "Eidolon of Blossoms",
        "Satyr Enchanter",
        "Sythis, Harvest's Hand",
        "Setessan Champion",
        "Sanctum Weaver",
        "Sterling Grove",
        "Sphere of Safety",
        "Ghostly Prison",
        "Propaganda",
        "Aura Shards",
        "Calix, Guided by Fate",
        "Hall of Heliod's Generosity",
        "Replenish",
        "Open the Vaults",
        "Resurgent Belief",
        "Starfield of Nyx",
        "Sigil of the Empty Throne",
        "Destiny Spinner",
        "Greater Auramancy",
        "Bear Umbra",
        "Enchanted Evening",
        "Hallowed Haunting",
        "Overgrowth",
        "Utopia Sprawl",
        "Wild Growth",
        "Fertile Ground",
        "Trace of Abundance",
        "Dawn's Reflection",
        "Market Festival",
        "Mana Bloom",
        "Farseek",
        "Nature's Lore",
        "Three Visits",
        "Rampant Growth",
        "Cultivate",
        "Kodama's Reach",
        "Sakura-Tribe Elder",
        "Wood Elves",
        "Farhaven Elf",
        "Solemn Simulacrum",
        "Chromatic Lantern",
        "Arcane Signet",
        "Sol Ring",
        "Fellwar Stone",
        "Command Tower",
        "Exotic Orchard",
        "City of Brass",
        "Mana Confluence",
        "Reflecting Pool",
        "Maze's End",
        "Gatecreeper Vine",
        "Gateway Plaza",
        "Circuitous Route",
        "Open the Gates",
        "Guild Summit",
        "Gates Ablaze",
        "Gate Colossus",
        "Gond Gate",
        "Baldur's Gate",
        "Basilica Gardens",
        "Plaza of Heroes",
        "Spara's Headquarters",
        "Raffine's Tower",
        "Xander's Lounge",
        "Ziatora's Proving Ground",
        "Jetmir's Garden",
        "Indatha Triome",
        "Ketria Triome",
        "Raugrin Triome",
        "Savai Triome",
        "Zagoth Triome",
        "The World Tree",
        "Valgavoth's Lair",
        "Nowhere to Run",
        "Cursed Recording",
        "Fear of Isolation",
        "Fear of Sleep Parasites",
        "Enduring Innocence",
        "Enduring Curiosity",
        "Enduring Courage",
        "Enduring Vitality",
        "Enduring Tenacity",
        "Toby, Beastie Befriender",
        "Overlord of the Hauntwoods",
        "Overlord of the Balemurk",
        "Overlord of the Boilerbilges",
        "Overlord of the Floodpits",
        "Overlord of the Mistmoors",
        "Rip, Spawn Hunter",
        "The Master of Keys",
        "Dollmaker's Shop // Porcelain Gallery",
        "Walk-In Closet // Forgotten Cellar",
        "Smuggler's Hideout // Covert Entrance",
    ]
    print("\nSEED LOOKUP")
    found, missing = [], []
    for name in seeds:
        info = get_oracle_card(name)
        if not info:
            missing.append(name)
            continue
        ci = set(info.get("color_identity") or [])
        if not ci.issubset(WUBRG):
            missing.append(name + " (illegal id)")
            continue
        found.append(info["name"])
        print(f"  OK {info['name']}")
    print(f"\nfound={len(found)} missing={len(missing)}")
    for m in missing:
        print(f"  MISS {m}")
    conn.close()


if __name__ == "__main__":
    main()
