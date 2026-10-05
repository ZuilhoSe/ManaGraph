#!/usr/bin/env python3
"""Fase 1, passos 3–4: cobertura da álgebra de operadores sobre os scripts do Forge.

Cruza o inventário de átomos (forge_ability_inventory.py) com o mapeamento
data/ontology/operator_algebra_v1.yaml e classifica cada carta:

  strict   todos os átomos em resource/event/modifier/mask/converter/alt_cost/control
  approx   strict + stochastic (valor esperado) + param (escolha fixada no deck)
  out      algum átomo numa família fora (copy, layers, turn, ...) ou não mapeado

Aceite da Fase 1: ≥ 80% das cartas legais em Commander abstraíveis.
Parada: < 60% → repensar a granularidade da álgebra.

Uso:
  python scripts/forge_algebra_coverage.py            # escreve ALGEBRA_COVERAGE.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from forge_ability_inventory import catalog_legal_cards

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY_DIR = PROJECT_ROOT / "data" / "ontology"

# Fallback when the YAML predates `operator_classes`.
CORE_CLASSES = {"resource", "event", "modifier", "mask", "converter", "alt_cost", "control"}
APPROX_CLASSES = {"stochastic", "param"}
ACCEPT, STOP = 0.80, 0.60
DEFERRED: dict[str, str] = {}


def load_algebra(path: Path) -> tuple[str, dict[str, str], set[str]]:
    """Atom → class (`resource`, `out:copy`, ...) and the generic wrapper atoms.

    Also sets CORE_CLASSES / APPROX_CLASSES from the contract's `operator_classes`.
    """
    global CORE_CLASSES, APPROX_CLASSES, DEFERRED
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    DEFERRED = dict(data.get("deferred_valuation") or {})
    classes = data.get("operator_classes") or {}
    if classes:
        CORE_CLASSES = set(classes.get("core") or [])
        APPROX_CLASSES = set(classes.get("approx") or [])
    mapping: dict[str, str] = {}
    for kind, groups in (data.get("atoms") or {}).items():
        for key, names in (groups or {}).items():
            cls = key.split(".", 1)[0]  # `resource.zones` groups extra resource atoms
            for name in names or []:
                atom = f"{kind}:{name}"
                if atom in mapping and mapping[atom] != cls:
                    raise ValueError(f"{atom} mapped twice: {mapping[atom]} / {cls}")
                mapping[atom] = cls
    return str(data.get("version", "")), mapping, set(data.get("generic") or [])


def classify_card(atoms: list[str], mapping: dict[str, str]) -> tuple[str, set[str]]:
    """('strict' | 'approx' | 'out', blocking classes) for one card."""
    classes = {mapping.get(atom, "unmapped") for atom in atoms}
    blocking = {cls for cls in classes if cls not in CORE_CLASSES | APPROX_CLASSES}
    if blocking:
        return "out", blocking
    return ("approx" if classes & APPROX_CLASSES else "strict"), set()


def coverage(inventory: dict, mapping: dict[str, str], generic: set[str] | None = None) -> dict:
    generic = generic or set()
    status = Counter()
    by_class = Counter()
    pessimistic_in = 0
    families = Counter()
    unmapped = Counter()
    sole_blocker = Counter()  # cards that one atom alone keeps out
    examples: dict[str, list[str]] = defaultdict(list)
    cards = [c for c in inventory["cards"] if c["commander_legal"]]
    for card in cards:
        verdict, blocking = classify_card(card["atoms"], mapping)
        status[verdict] += 1
        for cls in {mapping.get(atom, "unmapped") for atom in card["atoms"]}:
            by_class[cls] += 1
        if verdict != "out":
            pessimistic_in += not (generic & set(card["atoms"]))
            continue
        for family in blocking:
            families[family] += 1
            if len(examples[family]) < 8:
                examples[family].append(card["name"])
        out_atoms = [
            a for a in card["atoms"]
            if mapping.get(a, "unmapped") not in CORE_CLASSES | APPROX_CLASSES
        ]
        for atom in out_atoms:
            if atom not in mapping:
                unmapped[atom] += 1
        if len(out_atoms) == 1:
            sole_blocker[out_atoms[0]] += 1
    total = len(cards)
    return {
        "total": total,
        "status": dict(status),
        "strict_pct": status["strict"] / total if total else 0.0,
        "approx_pct": (status["strict"] + status["approx"]) / total if total else 0.0,
        "pessimistic_pct": pessimistic_in / total if total else 0.0,
        "families": families,
        "unmapped": unmapped,
        "sole_blocker": sole_blocker,
        "examples": examples,
        "by_class": by_class,
    }


def missing_scripts(inventory: dict, db_path: str | None = None) -> tuple[int, int]:
    """(commander-legal cards in the catalog, of which without a Forge script)."""
    legal = catalog_legal_cards(db_path) if db_path else catalog_legal_cards()
    forge = {c["name"].lower() for c in inventory["cards"]}
    missing = [n for n in legal if n.lower() not in forge and n.split(" // ")[0].lower() not in forge]
    return len(legal), len(missing)


def render(report: dict, version: str, inventory: dict, mapping: dict[str, str]) -> str:
    approx = report["approx_pct"]
    verdict = (
        "ACEITE (≥ 80%)" if approx >= ACCEPT
        else "PARADA (< 60%): repensar a granularidade" if approx < STOP
        else "ZONA CINZA (60–80%): seguir, mas atacar as famílias fora"
    )
    atom_counts = inventory["atom_card_counts_commander"]
    mapped = sum(1 for a in atom_counts if a in mapping)
    lines = [
        "# Cobertura da álgebra de operadores (Fase 1)",
        "",
        f"Gerado por `scripts/forge_algebra_coverage.py` · álgebra `{version}` "
        f"(`data/ontology/operator_algebra_v1.yaml`) · cardsfolder `{inventory['cardsfolder']}`.",
        "",
        "Nível medido: **abstraível** — todo tipo de habilidade da carta cabe na álgebra.",
        "Fidelidade (o operador prevê o Δ do Forge) é a Fase 3.",
        "",
        "## Resultado",
        "",
        "| | cartas | % |",
        "|---|---:|---:|",
        f"| legais em Commander (com script) | {report['total']} | 100% |",
        *(
            [f"| legais no catálogo sem script no Forge (fora do denominador) | "
             f"{report['missing'][1]} | {report['missing'][1] / report['missing'][0]:.1%} do catálogo |"]
            if report.get("missing") and report["missing"][0] else []
        ),
        f"| strict | {report['status'].get('strict', 0)} | {report['strict_pct']:.1%} |",
        f"| strict + approx (dado/moeda, escolha fixável, minimax do oponente) | "
        f"{report['status'].get('strict', 0) + report['status'].get('approx', 0)} | {approx:.1%} |",
        f"| fora | {report['status'].get('out', 0)} | {1 - approx:.1%} |",
        f"| cota pessimista (invólucros genéricos contados como fora) | | "
        f"{report['pessimistic_pct']:.1%} |",
        "",
        "A cota pessimista trata como fora os átomos cujo significado depende dos",
        "parâmetros (`generic:` no YAML: Animate, ReplaceEffect, Moved, Play, MayPlay, ...).",
        "Ela é o piso honesto até a Fase 2 ler esses parâmetros.",
        "",
        f"**Veredito:** {verdict}.",
        "",
        f"Átomos distintos nas cartas legais: {len(atom_counts)}; mapeados: {mapped}.",
        "",
        "## Classes com valor diferido",
        "",
        "Semântica definida na álgebra, mas o valor depende de fases posteriores.",
        "Dentro da álgebra não quer dizer resolvido: estas cartas só são avaliadas",
        "de verdade quando a fase indicada existir.",
        "",
        "| classe | cartas | quem resolve o valor |",
        "|---|---:|---|",
        *[
            f"| `{cls}` | {report['by_class'].get(cls, 0)} | {why} |"
            for cls, why in DEFERRED.items()
        ],
        "",
        "## Famílias fora da álgebra",
        "",
        "Uma carta pode estar em mais de uma família.",
        "",
        "| família | cartas | exemplos |",
        "|---|---:|---|",
    ]
    for family, n in report["families"].most_common():
        lines.append(f"| `{family}` | {n} | {', '.join(report['examples'][family][:5])} |")
    lines += [
        "",
        "## Átomos que mais quebram cartas sozinhos",
        "",
        "Cartas que ficariam abstraíveis se só esse átomo entrasse na álgebra —",
        "a ordem de ataque para subir a cobertura.",
        "",
        "| átomo | classe | cartas |",
        "|---|---|---:|",
    ]
    for atom, n in report["sole_blocker"].most_common(30):
        lines.append(f"| `{atom}` | {mapping.get(atom, 'unmapped')} | {n} |")
    lines += [
        "",
        "## Átomos não mapeados (revisar)",
        "",
        "| átomo | cartas |",
        "|---|---:|",
    ]
    for atom, n in report["unmapped"].most_common(40):
        lines.append(f"| `{atom}` | {n} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", type=Path, default=ONTOLOGY_DIR / "forge_atoms_v1.json")
    parser.add_argument("--algebra", type=Path, default=ONTOLOGY_DIR / "operator_algebra_v1.yaml")
    parser.add_argument("--out", type=Path, default=ONTOLOGY_DIR / "ALGEBRA_COVERAGE.md")
    args = parser.parse_args(argv)
    with open(args.inventory, encoding="utf-8") as handle:
        inventory = json.load(handle)
    version, mapping, generic = load_algebra(args.algebra)
    report = coverage(inventory, mapping, generic)
    report["missing"] = missing_scripts(inventory)
    args.out.write_text(render(report, version, inventory, mapping), encoding="utf-8")
    print(
        f"strict {report["strict_pct"]:.1%} | approx {report['approx_pct']:.1%} "
        f"| pessimistic {report['pessimistic_pct']:.1%} "
        f"of {report['total']} commander-legal cards -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
