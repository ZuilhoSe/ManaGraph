#!/usr/bin/env python3
"""Fase 1, passo 1: inventário dos tipos de habilidade nos scripts do Forge.

Lê o cardsfolder e reduz cada carta a "átomos" — os tipos de habilidade que o
motor do Forge executa:

    api:<ApiType>        A: AB$/SP$ e SVar: DB$ (o que a habilidade faz)
    trigger:<Mode>       T: Mode$ (quando dispara)
    static:<Mode>        S: Mode$ (efeito contínuo)
    replacement:<Event>  R: Event$ (efeito de substituição)
    keyword:<Name>       K: (palavra-chave)
    cost:<Kind>          partes não-mana de Cost$ (Sac, PayLife, Discard, ...)

A cobertura da álgebra de operadores (passo 3) é medida sobre esses átomos:
uma carta é abstraível quando todos os seus átomos estão na álgebra.

Uso:
  python scripts/forge_ability_inventory.py --out data/ontology/forge_atoms_v1.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from catalog import DB_NAME  # noqa: E402

DEFAULT_CARDSFOLDER = Path(r"C:\Users\segun\Documents\forge\forge-gui\res\cardsfolder")

_API_RE = re.compile(r"^(?:AB|SP|DB)\$\s*([A-Za-z]+)")
_PARAM_RE = re.compile(r"(\w+)\$\s*([^|]*)")
_COST_TOKEN_RE = re.compile(r"[A-Za-z]+<[^>]*>|\S+")
_COST_PART_RE = re.compile(r"([A-Za-z]+)")
# Mana symbols as Forge writes them: 2, R, X, hybrid RW / W/U, phyrexian RP / R/P.
_MANA_TOKEN_RE = re.compile(r"^(?:[0-9]+|[WUBRGCSX]{1,2}|[WUBRGC2]/?[WUBRGP])$")
# Continuous statics range from "+1/+1" to "loses all abilities"; the effect keys
# say which (Affected$/Condition$/... only select and gate).
CONTINUOUS_EFFECT_KEYS = {
    "AddPower", "AddToughness", "AddKeyword", "AddHiddenKeyword", "AddAbility",
    "AddTrigger", "AddStaticAbility", "AddReplacementEffect", "AddSVar", "AddType",
    "AddColor", "AddAllCreatureTypes", "SetPower", "SetToughness", "SetColor", "SetName",
    "CharacteristicDefining", "RemoveAllAbilities", "RemoveKeyword", "RemoveCardTypes",
    "RemoveCreatureTypes", "RemoveLandTypes", "RemoveArtifactTypes", "RemoveType",
    "GainControl", "GainsAbilitiesOf", "GainsAbilitiesOfZones", "GainsAbilitiesOfDefined",
    "GainsValidAbilities", "MayPlay", "MayLookAt", "MayPlayWithoutManaCost",
    "MayPlayAltManaCost", "MayPlayWithFlash", "SetMaxHandSize", "RaiseMaxHandSize",
    "AdjustLandPlays", "Goad", "CanBlockAmount", "CanBlockAny", "RaiseCost",
    "CantHaveKeyword", "TopCardOfLibraryIs", "DeclaresBlockers",
}


def _params(body: str) -> dict[str, str]:
    return {key: value.strip() for key, value in _PARAM_RE.findall(body)}


def _cost_atoms(cost: str) -> set[str]:
    """Non-mana cost kinds: `1 T Sac<1/Creature>` → {'cost:Sac', 'cost:T'}."""
    atoms = set()
    for token in _COST_TOKEN_RE.findall(cost):
        if token in ("T", "Q"):
            atoms.add(f"cost:{'Tap' if token == 'T' else 'Untap'}")
            continue
        if _MANA_TOKEN_RE.match(token):
            continue
        match = _COST_PART_RE.match(token)
        if match:
            atoms.add(f"cost:{match.group(1)}")
    return atoms


def _api_atom(api: str, body: str) -> str:
    """`ChangeZone` means nothing without its zones: Graveyard>Battlefield is
    reanimation, Library>Hand hidden is a tutor, Battlefield>Exile is removal."""
    if api in ("ChangeZone", "ChangeZoneAll"):
        params = _params(body)
        origin = params.get("Origin", "Any").split(",")[0].strip() or "Any"
        destination = params.get("Destination", "Any").split(",")[0].strip() or "Any"
        return f"api:{api}.{origin}>{destination}"
    return f"api:{api}"


def _static_atoms(body: str) -> set[str]:
    params = _params(body)
    mode = params.get("Mode", "")
    atoms = set()
    for part in mode.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "Continuous":
            keys = sorted(CONTINUOUS_EFFECT_KEYS & params.keys())
            atoms |= {f"static:Continuous.{key}" for key in keys} or {"static:Continuous"}
        else:
            atoms.add(f"static:{part}")
    return atoms


def _keyword_atom(value: str) -> str:
    keyword = value.split(":", 1)[0].strip()
    if keyword.startswith("Protection"):
        return "keyword:Protection"
    if keyword.endswith("walk") and keyword != "Landwalk":
        return "keyword:Landwalk"
    return f"keyword:{keyword}"


def card_atoms(lines: list[str]) -> tuple[str, set[str], dict[str, str]]:
    """Name of the front face, its atoms, and the first face's metadata."""
    name = ""
    atoms: set[str] = set()
    meta: dict[str, str] = {}
    svars: dict[str, str] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("ALTERNATE"):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "Name" and not name:
            name = value
        if key in ("ManaCost", "Types", "PT") and key not in meta:
            meta[key] = value
        if key == "SVar":
            svar_name, _, body = value.partition(":")
            svars[svar_name] = body
            match = _API_RE.match(body)
            if match:
                atoms.add(_api_atom(match.group(1), body))
                cost = _params(body).get("Cost")
                if cost:
                    atoms |= _cost_atoms(cost)
            elif body.startswith("Mode$"):
                # Triggers/statics granted by Effect$, Animate$, AddTrigger$, ...
                svar_params = _params(body)
                if "Execute" in svar_params:
                    atoms.add(f"trigger:{svar_params.get('Mode', '')}")
                else:
                    atoms |= _static_atoms(body)
            elif body.startswith("Event$"):
                atoms.add(f"replacement:{_params(body).get('Event', '')}")
        elif key == "A":
            match = _API_RE.match(value)
            if match:
                atoms.add(_api_atom(match.group(1), value))
            cost = _params(value).get("Cost")
            if cost:
                atoms |= _cost_atoms(cost)
        elif key == "T":
            mode = _params(value).get("Mode")
            if mode:
                atoms.add(f"trigger:{mode}")
        elif key == "S":
            atoms |= _static_atoms(value)
        elif key == "R":
            event = _params(value).get("Event")
            if event:
                atoms.add(f"replacement:{event}")
        elif key == "K":
            if value.strip():
                atoms.add(_keyword_atom(value))
    return name, atoms, meta


def catalog_legal_cards(db_path: str = DB_NAME) -> list[str]:
    """Catalog names legal in Commander."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name, legalities FROM cards").fetchall()
    finally:
        conn.close()
    names = []
    for name, legalities in rows:
        try:
            legal = (json.loads(legalities or "{}") or {}).get("commander") == "legal"
        except (TypeError, ValueError):
            legal = False
        if legal and name:
            names.append(name)
    return names


def commander_legal_names(db_path: str) -> set[str]:
    """Lowercased names (and front faces) legal in Commander, from the catalog."""
    names = set()
    for name in catalog_legal_cards(db_path):
        names.add(name.lower())
        names.add(name.split(" // ")[0].lower())
    return names


def inventory(cardsfolder: Path, db_path: str) -> dict:
    legal = commander_legal_names(db_path)
    cards = []
    for path in sorted(cardsfolder.rglob("*.txt")):
        with open(path, encoding="utf-8", errors="replace") as handle:
            name, atoms, meta = card_atoms(handle.readlines())
        if not name:
            continue
        cards.append({
            "name": name,
            "file": str(path.relative_to(cardsfolder)),
            "types": meta.get("Types", ""),
            "commander_legal": name.lower() in legal,
            "atoms": sorted(atoms),
        })
    counts = Counter(a for c in cards for a in c["atoms"])
    legal_counts = Counter(a for c in cards if c["commander_legal"] for a in c["atoms"])
    return {
        "cardsfolder": str(cardsfolder),
        "cards_total": len(cards),
        "cards_commander_legal": sum(c["commander_legal"] for c in cards),
        "atom_card_counts": dict(counts.most_common()),
        "atom_card_counts_commander": dict(legal_counts.most_common()),
        "cards": cards,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cardsfolder", type=Path, default=DEFAULT_CARDSFOLDER)
    parser.add_argument("--db", default=DB_NAME)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data/ontology/forge_atoms_v1.json")
    args = parser.parse_args(argv)
    if not args.cardsfolder.is_dir():
        print(f"cardsfolder not found: {args.cardsfolder}")
        return 1
    report = inventory(args.cardsfolder, args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False)
    kinds = Counter(a.split(":", 1)[0] for a in report["atom_card_counts"])
    print(
        f"{report['cards_total']} cards ({report['cards_commander_legal']} commander-legal), "
        f"{len(report['atom_card_counts'])} distinct atoms: {dict(kinds)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
