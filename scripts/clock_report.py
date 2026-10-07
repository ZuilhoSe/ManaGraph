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
from dataclasses import replace
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
    sources = f"{r.life_sources.get('combat', 0):.0f} / {r.life_sources.get('direct', 0):.0f}"
    return (f"| {name} | T{r.turn:.2f} | {r.turn_var:.2f} | {r.censored:.0%} | {r.damage_by[6]:.1f} | "
            f"{r.damage_by[8]:.1f} | {sources} | {r.routes.get('mill', 0):.0%} | {r.wasted_mana:.2f} | {terminals} |")


HEADER = ["| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto "
          "| mill até T | mana desperdiçada/turno | terminais |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]


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


def _bare(f: ClockCard) -> ClockCard:
    """The card without its type line and cost colours (they carry no clock value by themselves)."""
    return replace(f, types=frozenset(), subtypes=frozenset(), pips=())


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
            if f.power > 0 and _bare(f) == ClockCard(name=f.name, cmc=f.cmc, creature=True, power=f.power,
                                                toughness=f.toughness, haste=f.haste, evasive=f.evasive,
                                                trample=f.trample, double_strike=f.double_strike):
                creatures[cmc].append(f)
        elif ("Instant" in (type_line or "") or "Sorcery" in (type_line or "")) and 1 <= cmc <= 4 and len(utility) < 15:
            compiled = load_card(name)
            if compiled is None:
                continue
            f = clock_features(compiled)
            if _bare(f) == ClockCard(name=f.name, cmc=f.cmc):
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


# ---------------------------------------------------------------------------
# Routes beyond combat (Fase 2.5b)
# ---------------------------------------------------------------------------

# Neutral filler: a real card with no effect the clock reads, so each scenario shows
# its own route instead of a pile of attacking bodies.
FILLER = "Cancel"
BASICS_BRW = ["Swamp"] * 13 + ["Mountain"] * 12 + ["Plains"] * 12
TOKENS = ["Raise the Alarm", "Dragon Fodder", "Krenko's Command", "Bitterblossom", "Spectral Procession",
          "Lingering Souls", "Siege-Gang Commander", "Hordeling Outburst", "Beetleback Chief", "Battle Screech",
          "Midnight Haunting"]
OUTLETS = ["Goblin Bombardment", "Viscera Seer", "Carrion Feeder", "Ashnod's Altar", "Phyrexian Altar",
           "Bloodthrone Vampire"]
DEATH_PAYOFFS = ["Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant", "Falkenrath Noble", "Bastion of Remembrance",
                 "Mirkwood Bats", "Judith, the Scourge Diva", "Elas il-Kor, Sadistic Pilgrim", "Syr Konrad, the Grim"]
CHEAP_SPELLS = ["Opt", "Lightning Bolt", "Shock", "Brainstorm", "Ponder", "Preordain", "Consider", "Chain Lightning",
                "Burst Lightning", "Expedite", "Gitaxian Probe", "Manamorphose", "Thought Scour"] * 2
SPELL_PAYOFFS = ["Guttersnipe", "Electrostatic Field", "Firebrand Archer", "Kessig Flamebreather", "Thermo-Alchemist",
                 "Young Pyromancer", "Talrand, Sky Summoner"]
GATES = ["Azorius Guildgate", "Boros Guildgate", "Dimir Guildgate", "Golgari Guildgate", "Gruul Guildgate",
         "Izzet Guildgate", "Orzhov Guildgate", "Rakdos Guildgate", "Selesnya Guildgate", "Simic Guildgate",
         "Gond Gate", "Black Dragon Gate", "Citadel Gate", "Heap Gate", "Baldur's Gate"]
MILL = ["Hedron Crab", "Ruin Crab", "Psychic Corrosion", "Jace's Erasure", "Tome Scour", "Archive Trap",
        "Traumatize", "Glimpse the Unthinkable", "Thought Scour", "Altar of Dementia"]
SELF_MILL = ["Hedron Crab", "Ruin Crab", "Jace's Erasure", "Tome Scour", "Glimpse the Unthinkable", "Traumatize",
             "Stitcher's Supplier", "Armored Skaab", "Archive Trap", "Psychic Corrosion"]
EMPTY_LIBRARY_WINS = ["Thassa's Oracle", "Laboratory Maniac", "Jace, Wielder of Mysteries"]
LOOP = ["Sanguine Bond", "Exquisite Blood"]


def _deck(groups: list[list[str]], lands: list[str], n: int = 99) -> list[ClockCard]:
    cards = [card(x) for names in groups for x in names] + [card(x) for x in lands]
    return (cards + [card(FILLER)] * n)[:n]


def routes_beyond_combat(p: ClockParams) -> tuple[bool, list[str]]:
    long = replace(p, horizon=15)
    runs = {
        "aristocrats": (_deck([TOKENS, OUTLETS, DEATH_PAYOFFS], BASICS_BRW), p),
        "aristocrats sem payoffs de morte": (_deck([TOKENS, OUTLETS], BASICS_BRW), p),
        "spellslinger": (_deck([SPELL_PAYOFFS, CHEAP_SPELLS], ["Island"] * 18 + ["Mountain"] * 18), p),
        "spellslinger sem payoffs": (_deck([CHEAP_SPELLS], ["Island"] * 18 + ["Mountain"] * 18), p),
        "Maze's End + 15 Gates (T15)": (_deck([["Maze's End"]], GATES + ["Forest"] * 21), long),
        "Maze's End + 3 Gates (T15)": (_deck([["Maze's End"]], GATES[:3] + ["Forest"] * 33), long),
        "mill": (_deck([MILL], ["Island"] * 20 + ["Swamp"] * 16), p),
        "self-mill + Oracle/Maniac/Jace (T15)": (_deck([SELF_MILL, EMPTY_LIBRARY_WINS], ["Island"] * 36), long),
        "self-mill sem as vitórias (T15)": (_deck([SELF_MILL], ["Island"] * 36), long),
        "Sanguine Bond + Exquisite Blood": (_deck([LOOP * 3, DEATH_PAYOFFS], ["Swamp"] * 36), p),
        "só Sanguine Bond": (_deck([LOOP[:1] * 3, DEATH_PAYOFFS], ["Swamp"] * 36), p),
    }
    r = {name: simulate(deck, params=q) for name, (deck, q) in runs.items()}
    T, T15 = p.horizon, long.horizon

    def dmg(name: str) -> float:
        return r[name].damage_by[max(r[name].damage_by)]

    def direct_share(name: str) -> float:
        src = r[name].life_sources
        return src["direct"] / max(1e-9, src["direct"] + src["combat"])

    checks = [
        ("aristocrats: os payoffs de morte multiplicam o dano (≥ 2×) e a maior parte vem de fora do combate (≥ 40%)",
         dmg("aristocrats") >= 2 * dmg("aristocrats sem payoffs de morte") and direct_share("aristocrats") >= 0.4),
        ("spellslinger: os payoffs transformam mágicas em dano direto (direto > combate, e mais dano que sem eles)",
         direct_share("spellslinger") > 0.5 and dmg("spellslinger") > dmg("spellslinger sem payoffs")),
        ("Maze's End vence por vitória alternativa com ≥ 10 Gates de nomes diferentes, e nunca com 3",
         r["Maze's End + 15 Gates (T15)"].terminals.get("alt_win", 0) > 0
         and r["Maze's End + 3 Gates (T15)"].terminals.get("alt_win", 0) == 0),
        ("mill: o progresso vem da biblioteca dos oponentes, não da vida",
         r["mill"].routes["mill"] > r["mill"].routes["damage"]),
        ("self-mill: com Oracle/Maniac/Jace no deck, os \"jogador-alvo mói N\" passam a moer a própria biblioteca",
         r["self-mill + Oracle/Maniac/Jace (T15)"].routes["mill"] < r["self-mill sem as vitórias (T15)"].routes["mill"]),
        ("loop Sanguine Bond + Exquisite Blood fecha o jogo pelo dano direto (e só o Bond não)",
         r["Sanguine Bond + Exquisite Blood"].terminals.get("damage", 0)
         > r["só Sanguine Bond"].terminals.get("damage", 0)),
    ]
    ok = all(passed for _, passed in checks)
    lines = [
        f"Decks de 99 com cartas reais; o resto é `{FILLER}` (nenhum efeito que o relógio lê), para cada",
        f"cenário mostrar a própria rota. Horizonte T{T} (T{T15} onde indicado), {p.samples} ordens de compra.",
        "",
        *HEADER, *[_row(name, res) for name, res in r.items()], "",
        *[f"- {'ok' if passed else '**falhou**'}: {text}" for text, passed in checks], "",
        "Leitura: as rotas fora do combate são lentas em goldfish de 99 cartas com uma cópia de cada",
        "peça (o Maze's End é 1 carta em 99 e não há tutores aqui); o que o critério mede é a direção",
        "(a peça certa acelera a rota certa), não que o arquétipo vença sozinho até T12. O self-mill",
        "sem cartas que moem a biblioteca inteira (Hermit Druid etc., ainda não lidas) não esvazia 85",
        "cartas até T15; as vitórias de Oracle/Maniac/Jace estão cobertas por testes determinísticos",
        "(`tests/test_clock.py`).",
    ]
    return ok, lines


def alt_win_coverage() -> list[str]:
    """How many Commander-legal cards with WinsGame/LosesGame the clock can evaluate."""
    from operators.catalog import DEFAULT_CARDSFOLDER
    from operators.compile import compile_file
    from forge_ability_inventory import commander_legal_names

    legal = commander_legal_names(DB_NAME)
    read, unread = [], []
    for path in sorted(Path(DEFAULT_CARDSFOLDER).rglob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "WinsGame" not in text and "LosesGame" not in text:
            continue
        compiled = compile_file(path, Path(DEFAULT_CARDSFOLDER))
        if compiled is None or compiled.name.lower() not in legal:
            continue
        f = clock_features(compiled)
        hit = f.alt_win or f.wins or any(e.kind in ("win", "opp_loses") for a in f.abilities for e in a.effects)
        (read if hit else unread).append(compiled.name)
    total = len(read) + len(unread)
    return [
        f"Cartas legais em Commander com `WinsGame`/`LosesGame` no script: **{total}**. O relógio avalia "
        f"a condição de **{len(read)}** ({len(read) / max(1, total):.0%}) no estado simulado; as outras "
        "{0} ficam sem promessa (condição que o avaliador não lê, ou que depende do oponente).".format(len(unread)),
        "",
        f"- Lidas: {', '.join(read)}.",
        f"- Não lidas: {', '.join(unread)}.",
    ]


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
    ok4, l4 = routes_beyond_combat(p)
    verdict = "ACEITE" if ok1 and ok2 and ok3 and ok4 else "NÃO ACEITE"
    lines = [
        "# Relógio de dano (Fase 2.5)",
        "",
        "Gerado por `scripts/clock_report.py`. Cartas reais compiladas dos scripts do Forge;",
        f"{p.samples} ordens de compra por deck, horizonte T{p.horizon}, oponentes como estoque de",
        f"bloqueadores ({p.blocker_rate}/turno a partir do T{p.blockers_start}; bloqueio chump gasta o bloqueador).",
        "Vitória: 120 de vida tirada (combate, dano direto e drenos somam), 30 de veneno, 63 de dano",
        "de comandante, 255 cartas moídas (3 oponentes) ou vitória alternativa / \"oponentes perdem\"",
        "com a condição do script avaliada no estado. Goldfish abstrato: os oponentes não jogam, só",
        f"bloqueiam e nos tiram {p.incoming_damage:g} de vida por turno a partir do T{p.blockers_start + 1}.",
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
        f"## 4. Rotas além do combate — {'ok' if ok4 else 'falhou'}",
        "", *l4, "",
        "### Cobertura das vitórias alternativas",
        "", *alt_win_coverage(), "",
        "## Sensibilidade aos parâmetros do oponente",
        "",
        "`P_conectar` e a capacidade de bloqueio são parâmetros até a Fase 8 (risco do plano).",
        "A conclusão do critério 3 precisa valer na faixa toda:",
        "", *sensitivity(decks), "",
        "## Decks do repositório",
        "",
        "Decks gerados pelo sistema antigo (`data/`) e decks humanos (`lucas_plans/Decks/`).",
        "Todas as rotas contam, mas em goldfish: um deck de controle aparece lento aqui por",
        "construção (remoção e interação só ganham valor com oponentes que jogam, Fase 8).",
        "", *real_decks(p), "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{verdict} (ramp {ok1}, overrun {ok2}, swap {ok3}, routes {ok4}) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
