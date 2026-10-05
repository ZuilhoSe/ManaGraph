#!/usr/bin/env python3
"""Fase 3: fidelidade dos operadores contra o Forge.

Para uma amostra de cartas legais em Commander, monta um estado, resolve a
habilidade no Forge (tools/forge_oracle/ForgeOracle.java) e compara a variação
medida dos recursos com a prevista só a partir dos operadores compilados
(src/operators/predict.py).

Aceite da Fase 3: erro de previsão do Δ ≤ 10% em ≥ 85% dos casos de 1ª ordem.

Requer o Forge compilado (forge-gui-desktop jar-with-dependencies) e um JDK 17+.

Uso:
  python scripts/forge_oracle.py --per-api 40
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from catalog import DB_NAME  # noqa: E402
from forge_ability_inventory import DEFAULT_CARDSFOLDER, commander_legal_names  # noqa: E402
from operators.compile import compile_file  # noqa: E402
from operators.predict import compare, predict, setup_for  # noqa: E402

FORGE_ROOT = Path(os.environ.get("FORGE_ROOT", r"C:\Users\segun\Documents\forge"))
FORGE_JAR = FORGE_ROOT / "forge-gui-desktop" / "target" / "forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar"
JAVA_HOME = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\JetBrains\PyCharm 2025.2.4\jbr"))
HARNESS = PROJECT_ROOT / "tools" / "forge_oracle" / "ForgeOracle.java"
BUILD_DIR = PROJECT_ROOT / "tools" / "forge_oracle" / "build"
ONTOLOGY_DIR = PROJECT_ROOT / "data" / "ontology"
ACCEPT = 0.85


def candidates(cardsfolder: Path, db_path: str) -> list[tuple]:
    legal = commander_legal_names(db_path)
    out = []
    for path in sorted(cardsfolder.rglob("*.txt")):
        card = compile_file(path, cardsfolder)
        if not card.name or card.name.lower() not in legal or card.unresolved:
            continue
        setup = setup_for(card)
        if setup is None:
            continue
        expected = predict(card, setup)
        if expected is None:
            continue
        op = next(o for o in card.operators if o.source in ("spell", "activated"))
        api = "+".join(sorted({s.api for s in op.steps}))
        out.append((card, setup, expected, api))
    return out


def sample(cands: list[tuple], per_api: int, seed: int) -> list[tuple]:
    by_api = defaultdict(list)
    for c in cands:
        by_api[c[3]].append(c)
    rng = random.Random(seed)
    picked = []
    for api in sorted(by_api):
        group = by_api[api]
        picked += rng.sample(group, min(per_api, len(group)))
    return picked


def run_forge(experiments: list[tuple]) -> dict[str, tuple]:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    javac = JAVA_HOME / "bin" / "javac.exe"
    java = JAVA_HOME / "bin" / "java.exe"
    subprocess.run([str(javac), "-cp", str(FORGE_JAR), "-d", str(BUILD_DIR), str(HARNESS)], check=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        for i, (card, setup, _expected, _api) in enumerate(experiments):
            zone = "hand" if any(line.startswith("p1hand=") for line in setup.state) else "battlefield"
            handle.write(f"### {i}\n" + "\n".join(setup.state) + f"\n@action {card.name}|{zone}\n\n")
        exp_path = handle.name
    try:
        # Forge resolves res/ relative to the working directory.
        proc = subprocess.run(
            [str(java), "-Xmx4g", "-cp", f"{FORGE_JAR}{os.pathsep}{BUILD_DIR}", "ForgeOracle", exp_path],
            cwd=FORGE_ROOT / "forge-gui", capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    finally:
        os.unlink(exp_path)
    results = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit():
            results[parts[0]] = tuple(parts[1:])
    return results


def _vector(text: str) -> dict[str, int]:
    return {k: int(v) for k, v in (kv.split("=") for kv in text.split(","))}


def evaluate(experiments: list[tuple], results: dict[str, tuple]) -> dict:
    rows = []
    for i, (card, _setup, expected, api) in enumerate(experiments):
        res = results.get(str(i))
        if not res or res[0] != "OK":
            rows.append({"card": card.name, "api": api, "status": "oracle_error",
                         "detail": res[1] if res and len(res) > 1 else "no output"})
            continue
        before, after = _vector(res[1]), _vector(res[2])
        measured = {k: after[k] - before[k] for k in before if after[k] != before[k]}
        ok, misses = compare(expected, measured)
        rows.append({"card": card.name, "api": api, "status": "pass" if ok else "miss",
                     "predicted": expected, "measured": measured, "misses": misses})
    return {"rows": rows}


def render(report: dict, n_candidates: int, note: str = "") -> str:
    rows = report["rows"]
    judged = [r for r in rows if r["status"] != "oracle_error"]
    passed = [r for r in judged if r["status"] == "pass"]
    rate = len(passed) / len(judged) if judged else 0.0
    verdict = "ACEITE" if rate >= ACCEPT else "ABAIXO DO ACEITE"
    by_api = defaultdict(Counter)
    for r in rows:
        by_api[r["api"]][r["status"]] += 1
    lines = [
        "# Fidelidade dos operadores contra o Forge (Fase 3)",
        "",
        "Gerado por `scripts/forge_oracle.py`. Cada caso: um estado montado no Forge,",
        "a habilidade resolvida uma vez (só o efeito, sem pagar custo), e o Δ medido",
        "dos recursos comparado com o Δ previsto apenas a partir dos operadores",
        "compilados. Passa quando todo recurso previsto ou alterado está a ≤ 10%.",
        "",
        "## Resultado",
        "",
        *([note, ""] if note else []),
        "| | |",
        "|---|---:|",
        f"| cartas no escopo do preditor (1ª ordem, quantidades numéricas) | {n_candidates} |",
        f"| amostradas | {len(rows)} |",
        f"| erro do próprio oráculo (estado/habilidade não montou) | {len(rows) - len(judged)} |",
        f"| julgadas | {len(judged)} |",
        f"| **previsão correta** | **{len(passed)} ({rate:.1%})** |",
        "",
        f"**Veredito:** {verdict} (critério: ≥ {ACCEPT:.0%}).",
        "",
        "## Por tipo de efeito",
        "",
        "| API | pass | miss | erro do oráculo |",
        "|---|---:|---:|---:|",
    ]
    for api in sorted(by_api):
        c = by_api[api]
        lines.append(f"| `{api}` | {c['pass']} | {c['miss']} | {c['oracle_error']} |")
    lines += ["", "## Erros de previsão", "", "| carta | API | recurso: previsto → medido |", "|---|---|---|"]
    for r in rows:
        if r["status"] == "miss":
            diff = "; ".join(f"{k}: {p} → {m}" for k, (p, m) in sorted(r["misses"].items()))
            lines.append(f"| {r['card']} | `{r['api']}` | {diff} |")
    lines += ["", "## Erros do oráculo", "", "| carta | API | detalhe |", "|---|---|---|"]
    for r in rows:
        if r["status"] == "oracle_error":
            lines.append(f"| {r['card']} | `{r['api']}` | {r['detail'][:120]} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-api", type=int, default=40, help="Cards sampled per effect type.")
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument(
        "--exclude-seed", type=int, default=None,
        help="Held-out evaluation: drop the cards a sample with this seed would pick "
             "(the development sample) before sampling.",
    )
    parser.add_argument("--cardsfolder", type=Path, default=DEFAULT_CARDSFOLDER)
    parser.add_argument("--db", default=DB_NAME)
    args = parser.parse_args(argv)
    if not FORGE_JAR.exists():
        print(f"Forge jar not found: {FORGE_JAR} (build forge-gui-desktop first)")
        return 1
    cands = candidates(args.cardsfolder, args.db)
    if args.exclude_seed is not None:
        dev = {c[0].name for c in sample(cands, args.per_api, args.exclude_seed)}
        cands = [c for c in cands if c[0].name not in dev]
        print(f"held-out: excluded {len(dev)} development cards")
    experiments = sample(cands, args.per_api, args.seed)
    note = (
        f"**Avaliação separada:** semente {args.seed}, excluídas as {len(dev)} cartas da amostra "
        f"de desenvolvimento (semente {args.exclude_seed}) usada para ajustar o preditor. Tipos de "
        "efeito pequenos já foram inteiros para o desenvolvimento, então esta amostra cobre os maiores."
        if args.exclude_seed is not None else
        f"Amostra de desenvolvimento (semente {args.seed}): usada para ajustar o preditor; "
        "o número que vale é o da avaliação separada (`--exclude-seed`)."
    )
    print(f"{len(cands)} cards in predictor scope, {len(experiments)} sampled; running Forge...")
    report = evaluate(experiments, run_forge(experiments))
    (ONTOLOGY_DIR / "ORACLE_FIDELITY.md").write_text(render(report, len(cands), note), encoding="utf-8")
    with open(ONTOLOGY_DIR / "oracle_results_v1.json", "w", encoding="utf-8") as handle:
        json.dump(report["rows"], handle, ensure_ascii=False, indent=1, default=str)
    judged = [r for r in report["rows"] if r["status"] != "oracle_error"]
    passed = sum(r["status"] == "pass" for r in judged)
    print(f"pass {passed}/{len(judged)} ({passed / max(len(judged), 1):.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
