"""Thin wrapper: Marina Vendrell Rooms + Gate mana base via universal build_deck."""
from __future__ import annotations

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from build_deck import main

if __name__ == "__main__":
    argv = list(sys.argv[1:])
    if "--commander" not in argv:
        argv = [
            "--commander",
            "Marina Vendrell",
            "--query",
            "rooms enchantress unlock doors enchantment ramp gate mana base",
            *argv,
        ]
    raise SystemExit(main(argv))
