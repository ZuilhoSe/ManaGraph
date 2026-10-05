#!/usr/bin/env python3
"""Fase 2: compila os scripts do Forge em operadores e calcula a dominância.

Saídas:
  data/ontology/dominance_v1.json   relações (dominador, dominado) + equivalências
  data/ontology/DOMINANCE.md        estatísticas, pares canônicos, amostra de 100
                                    relações para conferência manual (aceite da Fase 2)

Uso:
  python scripts/build_dominance.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from catalog import DB_NAME  # noqa: E402
from forge_ability_inventory import DEFAULT_CARDSFOLDER, commander_legal_names  # noqa: E402
from operators.compile import compile_file  # noqa: E402
from operators.dominance import dominance  # noqa: E402

ONTOLOGY_DIR = PROJECT_ROOT / "data" / "ontology"

# (dominador esperado, dominado esperado) — aceite da Fase 2.
CANONICAL = [
    ("Lightning Bolt", "Shock"),
    ("Counterspell", "Cancel"),
]
# Pares que NÃO podem sair como dominância (trade-offs reais).
INCOMPARABLE = [
    ("Murder", "Doom Blade"),       # alvo mais amplo × BB
    ("Counterspell", "Mana Leak"),  # incondicional × mais barato
    ("Naturalize", "Disenchant"),   # cores diferentes
    ("Llanowar Elves", "Grizzly Bears"),
]


def build(cardsfolder: Path, db_path: str) -> tuple[list, dict]:
    legal = commander_legal_names(db_path)
    cards = []
    for path in sorted(cardsfolder.rglob("*.txt")):
        card = compile_file(path, cardsfolder)
        if card.name and card.name.lower() in legal:
            cards.append(card)
    relations, equivalents = dominance(cards)
    stats = {
        "cards": len(cards),
        "unresolved_cards": sum(1 for c in cards if c.unresolved),
        "unresolved_atoms": Counter(a for c in cards for a in c.unresolved),
        "relations": len(relations),
        "relations_same_subtypes": sum(1 for r in relations if not r.subtypes_differ),
        "dominated_cards": len({r.dominated for r in relations}),
        "equivalent_pairs": len(equivalents),
    }
    return cards, {"relations": relations, "equivalents": equivalents, "stats": stats}


def render(result: dict, cards: list) -> str:
    stats = result["stats"]
    rel = {(r.dominator, r.dominated) for r in result["relations"]}
    by_name = {c.name: c for c in cards}

    def check(a: str, b: str) -> str:
        if (a, b) in rel:
            return f"{a} > {b}"
        if (b, a) in rel:
            return f"{b} > {a}"
        return "incomparáveis"

    lines = [
        "# Dominância entre cartas (Fase 2)",
        "",
        "Gerado por `scripts/build_dominance.py` a partir dos scripts do Forge.",
        "A ≥ B: mesma assinatura (tipos + estrutura de operadores) e A é pelo menos",
        "tão boa em custo, velocidade, corpo, palavras-chave, condições, alvo e magnitude.",
        "Nenhum sinal de popularidade ou preço.",
        "",
        "## Números",
        "",
        "| | |",
        "|---|---:|",
        f"| cartas legais compiladas | {stats['cards']} |",
        f"| cartas com parâmetro genérico não lido (fora da comparação) | {stats['unresolved_cards']} |",
        f"| relações de dominância estrita | {stats['relations']} |",
        f"| … com os mesmos subtipos | {stats['relations_same_subtypes']} |",
        f"| cartas dominadas por pelo menos uma outra | {stats['dominated_cards']} |",
        f"| pares equivalentes (reimpressões funcionais) | {stats['equivalent_pairs']} |",
        "",
        "## Pares canônicos (aceite)",
        "",
        "| par | esperado | obtido | ok |",
        "|---|---|---|---|",
    ]
    ok_all = True
    for a, b in CANONICAL:
        got = check(a, b)
        ok = got == f"{a} > {b}"
        ok_all &= ok
        lines.append(f"| {a} × {b} | {a} > {b} | {got} | {'✅' if ok else '❌'} |")
    for a, b in INCOMPARABLE:
        got = check(a, b)
        ok = got == "incomparáveis"
        ok_all &= ok
        lines.append(f"| {a} × {b} | incomparáveis | {got} | {'✅' if ok else '❌'} |")
    lines += [
        "",
        f"**Pares canônicos: {'todos corretos' if ok_all else 'há erros'}.**",
        "",
        "## Amostra para conferência manual (100 relações, semente 7)",
        "",
        "Aceite da Fase 2: conferir à mão. Marque as erradas e o motivo.",
        "",
        "| # | dominador | custo | dominado | custo | subtipos diferem |",
        "|---:|---|---|---|---|---|",
    ]
    sample = random.Random(7).sample(result["relations"], min(100, len(result["relations"])))
    for i, r in enumerate(sample, 1):
        a, b = by_name[r.dominator], by_name[r.dominated]
        lines.append(
            f"| {i} | {r.dominator} | {a.mana_cost.raw} | {r.dominated} | {b.mana_cost.raw} | "
            f"{'sim' if r.subtypes_differ else ''} |"
        )
    lines += ["", "## Invólucros genéricos ainda não lidos", "", "| átomo | cartas |", "|---|---:|"]
    for atom, n in stats["unresolved_atoms"].most_common():
        lines.append(f"| `{atom}` | {n} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cardsfolder", type=Path, default=DEFAULT_CARDSFOLDER)
    parser.add_argument("--db", default=DB_NAME)
    args = parser.parse_args(argv)
    cards, result = build(args.cardsfolder, args.db)
    payload = {
        "relations": [
            {"dominator": r.dominator, "dominated": r.dominated, "subtypes_differ": r.subtypes_differ}
            for r in result["relations"]
        ],
        "equivalents": result["equivalents"],
    }
    with open(ONTOLOGY_DIR / "dominance_v1.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    (ONTOLOGY_DIR / "DOMINANCE.md").write_text(render(result, cards), encoding="utf-8")
    s = result["stats"]
    print(
        f"{s['cards']} cards, {s['relations']} relations, {s['dominated_cards']} dominated, "
        f"{s['equivalent_pairs']} equivalent pairs, {s['unresolved_cards']} unresolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
