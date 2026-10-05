#!/usr/bin/env python3
"""Fase 2.5: relógio de dano — critérios de aceite e decks reais.

Usa cartas reais compiladas dos scripts do Forge (operators.catalog) e o
simulador de relógio (operators.clock). Escreve data/ontology/CLOCK.md.

Critérios de aceite (plano, Fase 2.5):
  1. Ramp × topo de curva é interação positiva sem palavra em comum
     (o 7/7 de 7 manas dá ≈ 2 ataques a mais com duas rampas).
  2. O valor de um overrun cresce com o número de criaturas do deck.
  3. Num deck de criaturas verdes, trocar 10 criaturas grandes por 10 cartas
     de "draw" sem conversor em dano piora o relógio.

Uso:
  python scripts/clock_report.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from catalog import DB_NAME  # noqa: E402
from operators.catalog import load_card  # noqa: E402
from operators.clock import ClockCard, ClockParams, clock_features, simulate  # noqa: E402

OUT = PROJECT_ROOT / "data" / "ontology" / "CLOCK.md"
_LINE = re.compile(r"^(\d+)x?\s+(.+?)\s*$")

STOMP_RAMP = ["Llanowar Elves", "Elvish Mystic", "Fyndhorn Elves", "Rampant Growth", "Cultivate",
              "Kodama's Reach", "Wood Elves", "Farhaven Elf", "Nature's Lore", "Three Visits",
              "Sol Ring", "Arcane Signet"]
STOMP_BIG = ["Colossal Dreadmaw", "Pelakka Wurm", "Craw Wurm", "Krosan Colossus", "Thragtusk",
             "Garruk's Packleader", "Primeval Titan", "Terastodon", "Woodfall Primus", "Ancient Brontodon",
             "Ravenous Baloth", "Kalonian Tusker", "Centaur Courser", "Leatherback Baloth",
             "Grizzly Fate", "Hornet Queen", "Thorn Elemental", "Spined Wurm", "Gigantosaurus",
             "Ghalta, Primal Hunger"]
STOMP_FINISH = ["Overrun", "Craterhoof Behemoth", "Overwhelming Stampede"]
DRAW = ["Harmonize", "Divination", "Concentrate", "Tidings", "Inspiration", "Opportunity",
        "Sign in Blood", "Night's Whisper", "Phyrexian Arena", "Read the Bones"]


def card(name: str) -> ClockCard:
    compiled = load_card(name)
    if compiled is None:
        raise KeyError(f"no Forge script for {name!r}")
    return clock_features(compiled)


# Human lists do not mark the commander; the file name does (Portuguese for one).
COMMANDER_ALIASES = {"lightining": "lightning", "rebento de alara": "child of alara"}


def deck_from_file(path: Path, generated: bool) -> tuple[list[ClockCard], ClockCard | None, list[str]]:
    """Cards of a deck list and its commander (kept in the command zone, not the library).

    Generated lists start with the commander; for human lists it is the legendary
    creature whose name starts like the file name.
    """
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line.strip())
        if m:
            entries.append((int(m.group(1)), m.group(2)))
    stem = COMMANDER_ALIASES.get(path.stem.lower(), path.stem.lower())
    commander_name = entries[0][1] if generated and entries else None
    cards, missing, commander = [], [], None
    for qty, name in entries:
        compiled = load_card(name)
        if compiled is None:
            missing.append(name)
            continue
        is_commander = commander is None and (
            name == commander_name
            or (not generated and "Legendary" in compiled.supertypes and compiled.name.lower().startswith(stem))
        )
        if is_commander:
            commander = clock_features(compiled)
            qty -= 1
        cards += [clock_features(compiled)] * qty
    return cards, commander, missing


def _row(name: str, r) -> str:
    terminals = ", ".join(f"{k} {v:.0%}" for k, v in sorted(r.terminals.items(), key=lambda kv: -kv[1]))
    return (f"| {name} | T{r.turn:.2f} | {r.turn_var:.2f} | {r.censored:.0%} | {r.damage_by[6]:.1f} | "
            f"{r.damage_by[8]:.1f} | {r.wasted_mana:.2f} | {terminals} |")


HEADER = ["| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | mana desperdiçada/turno | terminais |",
          "|---|---:|---:|---:|---:|---:|---:|---|"]


def criterion_ramp(p: ClockParams) -> tuple[bool, list[str]]:
    forest, filler = card("Forest"), card("Island")  # Island: a land that is never needed (no effect here)
    big, ramp = card("Pelakka Wurm"), card("Rampant Growth")
    filler = ClockCard(name="(sem efeito)", cmc=3)
    no_block = ClockParams(horizon=10, blockers_cap=0)
    rows = []
    results = {}
    for label, top in (("nenhum", [filler, filler, filler]), ("só 7/7", [big, filler, filler]),
                       ("só 2 ramps", [ramp, ramp, filler]), ("7/7 + 2 ramps", [big, ramp, ramp])):
        order = [forest] * 5 + top + [forest] * 12
        results[label] = simulate([], params=no_block, order=order).damage_by[10]
        rows.append(f"| {label} | {results[label]:.0f} |")
    syn = results["7/7 + 2 ramps"] - results["só 7/7"] - results["só 2 ramps"] + results["nenhum"]
    attacks = (results["7/7 + 2 ramps"] - results["só 7/7"]) / big.power
    ok = syn > 0 and abs(attacks - 2) < 0.5
    lines = ["| mão inicial (5 Forests +) | dano até T10 |", "|---|---:|", *rows, "",
             f"syn(ramp, 7/7) = {syn:+.0f} de dano = **{attacks:.1f} ataques a mais** do Pelakka Wurm "
             "(7/7 trample, 7 manas), sem nenhuma palavra em comum com Rampant Growth. "
             "Ordem de compra fixa, sem bloqueadores, para isolar o efeito descrito no plano."]
    return ok, lines


def criterion_overrun(p: ClockParams) -> tuple[bool, list[str]]:
    forest, bear = card("Forest"), card("Grizzly Bears")
    overrun = card("Overrun")
    filler = ClockCard(name="(sem efeito)", cmc=3)
    lines = ["| criaturas no deck | dano T8 sem Overrun | com Overrun | ganho |", "|---:|---:|---:|---:|"]
    gains = []
    for k in (5, 15, 25, 35):
        base = [forest] * 37 + [bear] * k + [filler] * (62 - k)
        a = simulate(base, params=p).damage_by[8]
        b = simulate(base[:-1] + [overrun], params=p).damage_by[8]
        gains.append(b - a)
        lines.append(f"| {k} | {a:.1f} | {b:.1f} | {b - a:+.2f} |")
    ok = all(x < y for x, y in zip(gains, gains[1:]))
    lines += ["", "Ganho médio por partida (o Overrun é 1 carta em 99, comprado até T8 em ~15% delas)."]
    return ok, lines


def _green_pool() -> tuple[dict[int, list[ClockCard]], list[ClockCard]]:
    """Mono-green creatures by mana value and green non-clock spells, from the catalog,
    in name order — a deterministic deck, no hand-picking."""
    conn = sqlite3.connect(DB_NAME)
    rows = conn.execute(
        "SELECT name, type_line, cmc, color_identity, legalities FROM cards ORDER BY name"
    ).fetchall()
    conn.close()
    creatures: dict[int, list[ClockCard]] = {c: [] for c in range(2, 7)}
    utility: list[ClockCard] = []
    for name, type_line, cmc, identity, legalities in rows:
        try:
            if json.loads(identity or "[]") != ["G"] or json.loads(legalities or "{}").get("commander") != "legal":
                continue
        except ValueError:
            continue
        if "//" in name or "Legendary" in (type_line or ""):
            continue
        cmc = int(cmc or 0)
        if "Creature" in (type_line or "") and cmc in creatures and len(creatures[cmc]) < 6:
            compiled = load_card(name)
            if compiled is None or compiled.faces:
                continue
            f = clock_features(compiled)
            # A plain body: power, nothing else the clock reads (no tokens, ramp, draw, ...).
            if f.power > 0 and f == ClockCard(name=f.name, cmc=f.cmc, creature=True, power=f.power,
                                                toughness=f.toughness, haste=f.haste, evasive=f.evasive,
                                                trample=f.trample, double_strike=f.double_strike):
                creatures[cmc].append(f)
        elif ("Instant" in (type_line or "") or "Sorcery" in (type_line or "")) and 1 <= cmc <= 4 and len(utility) < 15:
            compiled = load_card(name)
            if compiled is None:
                continue
            f = clock_features(compiled)
            if f == ClockCard(name=f.name, cmc=f.cmc):
                utility.append(f)
    return creatures, utility


def criterion_swap(p: ClockParams) -> tuple[bool, list[str], dict]:
    """A green creature deck built from the catalog; swap its 10 strongest castable
    creatures (highest power, mana value ≤ 6) for 10 draw spells."""
    forest = card("Forest")
    ramp = [card(n) for n in STOMP_RAMP]
    finish = [card(n) for n in STOMP_FINISH]
    draw = [card(n) for n in DRAW]
    by_cmc, utility = _green_pool()
    creatures = [c for cmc in sorted(by_cmc) for c in by_cmc[cmc]]
    stomp = [forest] * 37 + ramp + creatures + finish + utility
    stomp = stomp[:99]
    strongest = sorted(creatures, key=lambda c: (-c.power, c.cmc, c.name))[:10]
    swapped = [c for c in stomp if c not in strongest] + draw
    results = {name: simulate(d, params=p) for name, d in
               (("stomp verde", stomp), ("10 mais fortes → 10 draw", swapped))}
    ok = (results["10 mais fortes → 10 draw"].damage_by[8] < results["stomp verde"].damage_by[8]
          and results["10 mais fortes → 10 draw"].turn >= results["stomp verde"].turn)
    lines = [*HEADER, *[_row(n, r) for n, r in results.items()], "",
             f"Deck: 37 Forests, {len(ramp)} ramp ({', '.join(STOMP_RAMP)}), {len(creatures)} criaturas "
             f"mono-verdes sem habilidades que o relógio lê (6 por mana value 2–6, ordem alfabética do "
             f"catálogo), finalizadores ({', '.join(STOMP_FINISH)}), {len(utility)} mágicas verdes sem "
             f"efeito no relógio. Trocadas: {', '.join(c.name for c in strongest)}. "
             f"Draw: {', '.join(DRAW)}."]
    return ok, lines, {"stomp": stomp, "swapped": swapped}


def finding_expensive_swap(p: ClockParams) -> list[str]:
    """The first, badly designed version of criterion 3, kept as a finding."""
    forest = card("Forest")
    ramp = [card(n) for n in STOMP_RAMP]
    big = [card(n) for n in STOMP_BIG]
    finish = [card(n) for n in STOMP_FINISH]
    draw = [card(n) for n in DRAW]
    bears = [card("Grizzly Bears")] * (99 - 36 - len(ramp) - len(big) - len(finish))
    stomp = [forest] * 36 + ramp + big + finish + bears
    swapped = [forest] * 36 + ramp + big[10:] + draw + finish + bears
    a, b = simulate(stomp, params=p), simulate(swapped, params=p)
    removed = ", ".join(f"{c.name} ({c.cmc})" for c in big[:10])
    return [*HEADER, _row("lista de ameaças à mão", a), _row("10 primeiras → 10 draw", b), "",
            f"Trocadas: {removed}. Várias custam 8–9 manas e quase não entram até T12; "
            "o modelo diz, corretamente, que compra barata vale mais que elas nesse horizonte. "
            "Por isso o critério 3 acima troca as mais fortes **lançáveis**."]


def sensitivity(decks: dict) -> list[str]:
    lines = ["| taxa de bloqueadores/turno | P_conectar (evasão) | dano T8 stomp | dano T8 trocado | stomp melhor? |",
             "|---:|---:|---:|---:|:---:|"]
    for rate in (0.25, 0.75, 1.25):
        for pe in (0.7, 0.85, 1.0):
            q = ClockParams(horizon=12, samples=200, blocker_rate=rate, p_evasive=pe)
            a = simulate(decks["stomp"], params=q).damage_by[8]
            b = simulate(decks["swapped"], params=q).damage_by[8]
            lines.append(f"| {rate} | {pe} | {a:.1f} | {b:.1f} | {'sim' if a > b else '**não**'} |")
    return lines


def real_decks(p: ClockParams) -> list[str]:
    lines = [*HEADER]
    sources = sorted((PROJECT_ROOT / "data").glob("deck_*.txt")) + sorted((PROJECT_ROOT / "lucas_plans" / "Decks").glob("*.txt"))
    notes = []
    for path in sources:
        generated = path.parent.name == "data"
        deck, commander, missing = deck_from_file(path, generated)
        if len(deck) < 60:
            notes.append(f"- `{path.name}`: só {len(deck)} cartas reconhecidas, fora da tabela.")
            continue
        origin = "gerado" if generated else "humano"
        label = f"{path.stem} ({origin}; comandante: {commander.name if commander else '?'})"
        lines.append(_row(label, simulate(deck, commander=commander, params=p)))
        if missing:
            notes.append(f"- `{path.name}`: sem script no Forge: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}")
    return lines + ([""] + notes if notes else [])


def main() -> int:
    p = ClockParams(horizon=12, samples=300)
    ok1, l1 = criterion_ramp(p)
    ok2, l2 = criterion_overrun(p)
    ok3, l3, decks = criterion_swap(p)
    verdict = "ACEITE" if ok1 and ok2 and ok3 else "NÃO ACEITE"
    lines = [
        "# Relógio de dano (Fase 2.5)",
        "",
        "Gerado por `scripts/clock_report.py`. Cartas reais compiladas dos scripts do Forge;",
        f"{p.samples} ordens de compra por deck, horizonte T{p.horizon}, oponentes como estoque de",
        f"bloqueadores ({p.blocker_rate}/turno a partir do T{p.blockers_start}; bloqueio chump gasta o bloqueador).",
        "Vitória: 120 de dano, 30 de veneno, 63 de dano de comandante ou biblioteca vazia",
        "(3 oponentes). Goldfish abstrato: os oponentes não jogam, só bloqueiam.",
        "",
        f"**Veredito:** {verdict}.",
        "",
        f"## 1. Ramp × topo de curva — {'ok' if ok1 else 'falhou'}",
        "", *l1, "",
        f"## 2. Overrun cresce com o número de criaturas — {'ok' if ok2 else 'falhou'}",
        "", *l2, "",
        f"## 3. Stomp vs trocar ameaças por compra — {'ok' if ok3 else 'falhou'}",
        "", *l3, "",
        "### Achado: a primeira versão deste teste",
        "", *finding_expensive_swap(p), "",
        "## Sensibilidade aos parâmetros do oponente",
        "",
        "`P_conectar` e a capacidade de bloqueio são parâmetros até a Fase 8 (risco do plano).",
        "A conclusão do critério 3 precisa valer na faixa toda:",
        "", *sensitivity(decks), "",
        "## Decks do repositório",
        "",
        "Decks gerados pelo sistema antigo (`data/`) e decks humanos (`lucas_plans/Decks/`).",
        "O relógio mede só a rota de dano/veneno/comandante/mill em goldfish: um deck de",
        "controle ou combo condicional aparece lento aqui por construção.",
        "", *real_decks(p), "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{verdict} (ramp {ok1}, overrun {ok2}, swap {ok3}) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
