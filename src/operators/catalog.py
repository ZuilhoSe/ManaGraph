"""Compile cards by name: a cached name → Forge script index over the cardsfolder."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from operators.compile import CardOps, compile_file

DEFAULT_CARDSFOLDER = Path(os.environ.get(
    "FORGE_CARDSFOLDER", r"C:\Users\segun\Documents\forge\forge-gui\res\cardsfolder"
))
INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "ontology" / "forge_index_v1.json"


def build_index(cardsfolder: Path = DEFAULT_CARDSFOLDER) -> dict[str, str]:
    """Lowercased card name (and front face) → script path relative to cardsfolder."""
    index: dict[str, str] = {}
    for path in cardsfolder.rglob("*.txt"):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("Name:"):
                    index.setdefault(line[5:].strip().lower(), str(path.relative_to(cardsfolder)))
                    break
    return index


@lru_cache(maxsize=1)
def _index(cardsfolder: str = str(DEFAULT_CARDSFOLDER)) -> dict[str, str]:
    if INDEX_PATH.exists():
        with open(INDEX_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("cardsfolder") == cardsfolder:
            return data["index"]
    index = build_index(Path(cardsfolder))
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as handle:
        json.dump({"cardsfolder": cardsfolder, "index": index}, handle)
    return index


@lru_cache(maxsize=None)
def load_card(name: str, cardsfolder: str = str(DEFAULT_CARDSFOLDER)) -> CardOps | None:
    """Compiled card by name (front face for "A // B"), or None if Forge has no script."""
    index = _index(cardsfolder)
    rel = index.get(name.lower()) or index.get(name.split(" // ")[0].lower())
    if not rel:
        return None
    return compile_file(Path(cardsfolder) / rel, Path(cardsfolder))
