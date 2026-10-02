#!/usr/bin/env python3
"""Fase 1 — auditoria de cobertura do Forge contra a álgebra de operadores.

Reads a local Forge ``cardsfolder`` (the same input scripts/mine_forge.py
uses), classifies every rules element of every card with
``src/operators/algebra.py`` and reports:

* the vocabulary (effect APIs, trigger modes, static modes, replacement
  events, keywords) with frequencies and class;
* card-level coverage: a card is *abstractable* when none of its elements is
  OUT or UNKNOWN;
* magnitude resolvability: share of RESOURCE effects whose amount is a
  constant (Phase 2 needs magnitudes);
* the out-of-algebra families and the cards that break the model most.

Nothing is written to the catalog. Output goes to ``--out`` (default
``eval/forge_coverage/``): ``report.md``, ``summary.json``, ``cards.csv``.

    python scripts/audit_forge_coverage.py --cardsfolder /path/to/forge-gui/res/cardsfolder
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mine_forge import parse_forge_text  # noqa: E402
from operators.algebra import (  # noqa: E402
    NON_TRADITIONAL_TYPES,
    OUT,
    RESOURCE,
    UNKNOWN,
    classify,
    static_value,
)

SKIP_DIRS = frozenset({"rebalanced", "upcoming"})  # Alchemy rebalances, unreleased
MAGNITUDE_KEYS = (
    "NumCards", "Amount", "NumDmg", "LifeAmount", "CounterNum", "TokenAmount",
    "ChangeNum", "NumAtt", "NumDef", "DigNum", "Num",
)
_INT_RE = re.compile(r"^[+-]?\d+$")
# Wrappers whose inner statics/replacements live in SVar strings the miner does
# not parse. In the algebra by type, but opaque: the strict metric excludes them.
OPAQUE = frozenset({("api", "Effect"), ("api", "ReplaceEffect")})


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _api_of(params: dict[str, Any]) -> str | None:
    for key, value in params.items():
        if key in ("AB", "SP", "DB") or key.endswith((":DB", ":AB", ":SP")):
            return _first(value)
    return None


def _magnitude_kind(params: dict[str, Any]) -> str:
    """constant | variable for one effect's amount (absent means 1: constant)."""
    for key in MAGNITUDE_KEYS:
        if key in params:
            raw = _first(params[key]).strip()
            return "constant" if _INT_RE.match(raw) else "variable"
    return "constant"


def card_elements(parsed: dict[str, Any]) -> list[dict[str, str]]:
    """Every classifiable rules element of a parsed Forge card."""
    out: list[dict[str, str]] = []
    for effect in parsed.get("effects") or []:
        prefix = effect.get("prefix")
        params = effect.get("params") or {}
        if prefix in ("A", "SVar"):
            api = _api_of(params)
            if not api:
                continue  # SVar that is a variable / AI hint, not an ability
            out.append({
                "kind": "api",
                "value": api,
                "class": classify("api", api),
                "magnitude": _magnitude_kind(params),
            })
        elif prefix == "S":
            mode = static_value(_first(params.get("Mode")) or "?", params)
            out.append({"kind": "static", "value": mode, "class": classify("static", mode)})
        elif prefix == "R":
            event = _first(params.get("Event")) or "?"
            out.append({"kind": "replacement", "value": event, "class": classify("replacement", event)})
        elif prefix == "K":
            keyword = str(effect.get("value") or "").split(":")[0].strip()
            out.append({"kind": "keyword", "value": keyword, "class": classify("keyword", keyword)})
    for trig in parsed.get("triggers") or []:
        mode = _first((trig.get("params") or {}).get("Mode")) or "?"
        out.append({"kind": "trigger", "value": mode, "class": classify("trigger", mode)})
    return out


def _types_of(parsed: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    for face in parsed.get("card", {}).get("faces") or []:
        raw = ((face.get("metadata") or {}).get("Types")) or ""
        types.update(str(raw).split())
    return types


def iter_scripts(root: Path, include_all: bool = False) -> Iterable[Path]:
    for path in sorted(root.rglob("*.txt")):
        rel = path.relative_to(root)
        if not include_all and rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        yield path


def audit(root: Path, include_all: bool = False, names: set[str] | None = None) -> dict[str, Any]:
    vocab: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    vocab_class: dict[tuple[str, str], str] = {}
    vocab_examples: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    cards: list[dict[str, Any]] = []
    excluded = collections.Counter()
    parse_errors = 0
    magnitude = collections.Counter()

    for path in iter_scripts(root, include_all):
        try:
            parsed = parse_forge_text(path.read_text(encoding="utf-8"), path.name)
        except Exception:  # noqa: BLE001 — a broken script is a data point, not a crash
            parse_errors += 1
            continue
        name = _first(parsed.get("card", {}).get("name")) or path.stem
        if names is not None and name.lower() not in names:
            excluded["not in catalog filter"] += 1
            continue
        nontrad = _types_of(parsed) & NON_TRADITIONAL_TYPES
        if nontrad:
            excluded[f"type:{sorted(nontrad)[0]}"] += 1
            continue
        elements = card_elements(parsed)
        classes = collections.Counter(e["class"] for e in elements)
        breaking = [e for e in elements if e["class"] in (OUT, UNKNOWN)]
        for e in elements:
            key = (e["kind"], e["value"])
            vocab[e["kind"]][e["value"]] += 1
            vocab_class[key] = e["class"]
            if len(vocab_examples[key]) < 3:
                vocab_examples[key].append(name)
            if e["class"] == RESOURCE:
                magnitude[e.get("magnitude", "constant")] += 1
        cards.append({
            "name": name,
            "file": str(path.relative_to(root)),
            "elements": len(elements),
            "abstractable": not breaking,
            "strict": not breaking and not any((e["kind"], e["value"]) in OPAQUE for e in elements),
            "out": sum(1 for e in breaking if e["class"] == OUT),
            "unknown": sum(1 for e in breaking if e["class"] == UNKNOWN),
            "breaking": sorted({f'{e["kind"]}:{e["value"]}' for e in breaking}),
            "classes": dict(classes),
        })

    total = len(cards)
    ok = sum(1 for c in cards if c["abstractable"])
    strict = sum(1 for c in cards if c["strict"])
    out_families = collections.Counter()
    unknown_families = collections.Counter()
    for c in cards:
        for item in c["breaking"]:
            kind, value = item.split(":", 1)
            if vocab_class.get((kind, value)) == OUT:
                out_families[item] += 1
            else:
                unknown_families[item] += 1
    worst = sorted(cards, key=lambda c: (-(c["out"] + c["unknown"]), c["name"]))[:40]
    vocab_rows = []
    for kind, counter in vocab.items():
        for value, count in counter.most_common():
            vocab_rows.append({
                "kind": kind,
                "value": value,
                "count": count,
                "class": vocab_class[(kind, value)],
                "examples": vocab_examples[(kind, value)],
            })
    magnitude_total = sum(magnitude.values()) or 1
    summary = {
        "cardsfolder": str(root),
        "cards": total,
        "abstractable": ok,
        "coverage": round(ok / total, 4) if total else 0.0,
        "coverage_strict": round(strict / total, 4) if total else 0.0,
        "excluded": dict(excluded),
        "parse_errors": parse_errors,
        "resource_effects": sum(magnitude.values()),
        "magnitude_constant_share": round(magnitude["constant"] / magnitude_total, 4),
        "element_classes": dict(collections.Counter(
            cls for (_kind, _value), cls in vocab_class.items()
        )),
        "element_occurrences_by_class": dict(sum(
            (collections.Counter(c["classes"]) for c in cards), collections.Counter()
        )),
        "vocabulary_size": {kind: len(counter) for kind, counter in vocab.items()},
        "vocabulary_unknown": sorted(
            f"{k}:{v}" for (k, v), cls in vocab_class.items() if cls == UNKNOWN
        ),
        "top_out_families": out_families.most_common(30),
        "top_unknown_families": unknown_families.most_common(30),
        "acceptance": {
            "target": 0.80,
            "stop_below": 0.60,
            "passed": (ok / total) >= 0.80 if total else False,
        },
    }
    return {"summary": summary, "cards": cards, "vocab": vocab_rows, "worst": worst}


def _forge_commit(root: Path) -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def write_report(result: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    s = result["summary"]
    (out_dir / "summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out_dir / "cards.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "file", "abstractable", "elements", "out", "unknown", "breaking"])
        for c in sorted(result["cards"], key=lambda c: c["name"]):
            writer.writerow([c["name"], c["file"], int(c["abstractable"]), c["elements"],
                             c["out"], c["unknown"], "; ".join(c["breaking"])])

    lines = [
        "# Auditoria de cobertura do Forge (Fase 1)",
        "",
        "Gerado por `scripts/audit_forge_coverage.py` contra `src/operators/algebra.py`.",
        "Plano: [`zuilho_plans/economia-de-operadores.md`](../../zuilho_plans/economia-de-operadores.md).",
        "",
        "## Resultado",
        "",
        f"- Corpus: Forge `{s.get('forge_commit') or 'commit desconhecido'}`"
        f"{' filtrado pelo catálogo (Commander-legal)' if s.get('catalog_filter') else ' (sem filtro de legalidade: catálogo Scryfall ausente)'}",
        f"- Cartas auditadas: **{s['cards']}** (excluídas: {sum(s['excluded'].values())}; "
        f"erros de parse: {s['parse_errors']})",
        f"- Abstraíveis (nenhum elemento OUT/UNKNOWN): **{s['abstractable']}** = "
        f"**{_pct(s['coverage'])}**",
        f"- Cobertura estrita (sem `Effect`/`ReplaceEffect` opacos): **{_pct(s['coverage_strict'])}**",
        f"- Critério de aceite ≥ 80%: **{'passou' if s['acceptance']['passed'] else 'não passou'}**"
        f" (parada abaixo de 60%)",
        f"- Efeitos de recurso: {s['resource_effects']}; com magnitude constante: "
        f"**{_pct(s['magnitude_constant_share'])}** (o resto depende de X / contagem do estado)",
        "",
        "Nível 1 (scriptada) e nível 2 (abstraível) estão medidos aqui. O nível 3",
        "(fiel: o operador prevê o Δ medido no Forge) é a Fase 3.",
        "",
        "**Leitura honesta:** esta é cobertura *por tipo de elemento*. Ela diz que todo",
        "elemento da carta tem lugar na álgebra, não que os parâmetros dele já viram um",
        "operador correto. É condição necessária para a Fase 2, não suficiente; o teste",
        "de suficiência é a fidelidade da Fase 3. Palavras-chave sem classe explícita",
        "contam como MODIFIER por padrão, e `Effect` esconde estáticas internas — por",
        "isso a métrica estrita.",
        "",
        "## Vocabulário",
        "",
        "| Tipo | Valores distintos |",
        "|---|---|",
    ]
    for kind, n in sorted(s["vocabulary_size"].items()):
        lines.append(f"| {kind} | {n} |")
    lines += ["", "Elementos sem classe (UNKNOWN), a revisar na álgebra:", ""]
    lines.append(", ".join(f"`{v}`" for v in s["vocabulary_unknown"]) or "_nenhum_")
    lines += ["", "## Famílias fora da álgebra (OUT), por nº de cartas", "", "| Elemento | Cartas |", "|---|---|"]
    for item, n in s["top_out_families"]:
        lines.append(f"| `{item}` | {n} |")
    if s["top_unknown_families"]:
        lines += ["", "## Elementos desconhecidos (UNKNOWN), por nº de cartas", "", "| Elemento | Cartas |", "|---|---|"]
        for item, n in s["top_unknown_families"]:
            lines.append(f"| `{item}` | {n} |")
    lines += ["", "## Cartas que mais quebram o modelo", "", "| Carta | OUT | UNKNOWN | Elementos |", "|---|---|---|---|"]
    for c in result["worst"][:25]:
        lines.append(f"| {c['name']} | {c['out']} | {c['unknown']} | {', '.join(f'`{b}`' for b in c['breaking'][:6])} |")
    lines += ["", "## Vocabulário completo (top 60 por frequência)", "", "| Tipo | Valor | Ocorrências | Classe |", "|---|---|---|---|"]
    for row in sorted(result["vocab"], key=lambda r: -r["count"])[:60]:
        lines.append(f"| {row['kind']} | `{row['value']}` | {row['count']} | {row['class']} |")
    lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _catalog_names(db_path: Path) -> set[str]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name, legalities FROM cards"
        ).fetchall()
    finally:
        conn.close()
    names: set[str] = set()
    for name, legal in rows:
        try:
            status = (json.loads(legal or "{}") or {}).get("commander")
        except (TypeError, ValueError):
            status = None
        if status in ("legal", "restricted", None):
            names.add(str(name).lower())
            for face in str(name).split(" // "):
                names.add(face.lower())
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cardsfolder", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "eval" / "forge_coverage")
    parser.add_argument("--include-all", action="store_true", help="also audit rebalanced/ and upcoming/")
    parser.add_argument(
        "--catalog", type=Path, default=None,
        help="restrict to Commander-legal names in this SQLite catalog (data/managraph.db)",
    )
    args = parser.parse_args(argv)
    if not args.cardsfolder.is_dir():
        parser.error(f"not a directory: {args.cardsfolder}")
    names = _catalog_names(args.catalog) if args.catalog else None
    result = audit(args.cardsfolder, include_all=args.include_all, names=names)
    result["summary"]["cardsfolder"] = "forge-gui/res/cardsfolder"
    result["summary"]["forge_commit"] = _forge_commit(args.cardsfolder)
    result["summary"]["catalog_filter"] = bool(names)
    write_report(result, args.out)
    s = result["summary"]
    print(f"cards={s['cards']} abstractable={s['abstractable']} coverage={_pct(s['coverage'])} "
          f"magnitude_constant={_pct(s['magnitude_constant_share'])}")
    print(f"report: {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
