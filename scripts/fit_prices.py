#!/usr/bin/env python3
"""Fase 4: preços latentes π e força de carta s_k.

Compila todas as cartas legais em Commander dos scripts do Forge, monta E_k − C_k
(operators.pricing), ajusta π por Huber com π_mana = 1 e efeito de era/produto,
e valida:

  1. previsão de mana value fora da amostra (5 dobras) melhor que a média por tipo;
  2. Mind's Eye com s_k claramente negativo (intervalo de bootstrap abaixo de zero);
  3. consistência com a dominância da Fase 2: quem domina não é mais fraco.

Escreve data/ontology/prices_v1.json (π, interceptos, calibração; versionado) e
data/ontology/PRICES.md.

Uso:
  python scripts/fit_prices.py [--bootstrap 30] [--folds 5]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from catalog import DB_NAME  # noqa: E402
from forge_ability_inventory import commander_legal_names  # noqa: E402
from operators.catalog import DEFAULT_CARDSFOLDER  # noqa: E402
from operators.compile import compile_file  # noqa: E402
from operators.pricing import (  # noqa: E402
    PRICES_PATH, CardVector, card_vector, commander_features, era_group, first_printings, fit_prices,
    mean_by_type_baseline, save_prices, fit_with_rule_floors, combat_keyword_damage, EPSILON,
)

EDITIONS = DEFAULT_CARDSFOLDER.parent / "editions"
OUT_MD = PROJECT_ROOT / "data" / "ontology" / "PRICES.md"
SHOWCASE = ["Sol Ring", "Lightning Bolt", "Swords to Plowshares", "Counterspell", "Divination", "Murder",
            "Doom Blade", "Llanowar Elves", "Rampant Growth", "Cultivate", "Grizzly Bears", "Hill Giant",
            "Serra Angel", "Shivan Dragon", "Colossal Dreadmaw", "Phyrexian Arena", "Necropotence",
            "Rhystic Study", "Smothering Tithe", "Esper Sentinel", "Guttersnipe", "Blood Artist",
            "Gray Merchant of Asphodel", "Mind's Eye"]


DAMAGE: dict[str, float] = {}  # clock damage of combat keywords per power point (set in main)


def fit(vecs, groups, **kw):
    """π with the rule floors: ε on every effect/cost, speed floor, clock floors on combat keywords."""
    return fit_with_rule_floors(vecs, groups, DAMAGE, **kw)


def load_cards() -> list:
    legal = commander_legal_names(DB_NAME)
    cards = []
    for path in sorted(DEFAULT_CARDSFOLDER.rglob("*.txt")):
        try:
            card = compile_file(path, DEFAULT_CARDSFOLDER)
        except Exception:  # noqa: BLE001 - a script the compiler cannot read is just skipped
            continue
        if card and card.name.lower() in legal:
            cards.append(card)
    return cards


def cross_validate(vecs: list[CardVector], groups: list[str], folds: int, seed: int = 1,
                   floors: dict[str, float] | None = None) -> dict:
    idx = list(range(len(vecs)))
    random.Random(seed).shuffle(idx)
    parts = [idx[i::folds] for i in range(folds)]
    err, base, mvs = [], [], []
    for k in range(folds):
        test = set(parts[k])
        train = [i for i in idx if i not in test]
        prices = fit([vecs[i] for i in train], [groups[i] for i in train], floors=floors)
        te = sorted(test)
        baseline = mean_by_type_baseline([vecs[i] for i in train], [vecs[i] for i in te])
        for i, b in zip(te, baseline):
            err.append(prices.predict(vecs[i].features, groups[i]) - vecs[i].mana_value)
            base.append(b - vecs[i].mana_value)
            mvs.append(vecs[i].mana_value)
    err, base, mvs = np.array(err), np.array(base), np.array(mvs)
    buckets = []
    for lo, hi, label in ((0, 1, "0–1"), (2, 2, "2"), (3, 3, "3"), (4, 4, "4"), (5, 6, "5–6"), (7, 99, "7+")):
        mask = (mvs >= lo) & (mvs <= hi)
        buckets.append((label, int(mask.sum()), float(np.mean(np.abs(err[mask]))), float(np.mean(np.abs(base[mask])))))
    return {"mae": float(np.mean(np.abs(err))), "baseline": float(np.mean(np.abs(base))),
            "rmse": float(np.sqrt(np.mean(err ** 2))), "baseline_rmse": float(np.sqrt(np.mean(base ** 2))),
            "buckets": buckets}


def bootstrap(vecs, groups, rounds: int, names: list[str], byname: dict, seed: int = 2) -> tuple[dict, dict]:
    """Percentile intervals for π and for s_k of the named cards."""
    rng = np.random.default_rng(seed)
    n = len(vecs)
    pis: dict[str, list[float]] = {}
    strengths: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(rounds):
        weights = rng.exponential(1.0, n)  # Bayesian bootstrap: every card keeps a positive weight
        weights *= n / weights.sum()
        prices = fit(vecs, groups, sample_weight=weights)
        for f, w in zip(prices.features, prices.weights):
            pis.setdefault(f, []).append(float(w))
        for name in names:
            vec = byname[name]
            strengths[name].append(prices.strength(vec.features, vec.mana_value))
    ci = lambda xs: (float(np.percentile(xs, 5)), float(np.percentile(xs, 95)))  # noqa: E731
    return {f: ci(v) for f, v in pis.items() if len(v) == rounds}, {k: ci(v) for k, v in strengths.items()}


def dominance_check(prices, byname: dict) -> tuple[int, int, list[str]]:
    path = PROJECT_ROOT / "data" / "ontology" / "dominance_v1.json"
    relations = json.loads(path.read_text(encoding="utf-8")).get("relations", [])
    agree = total = 0
    worst = []
    for rel in relations:
        a, b = byname.get(rel["dominator"]), byname.get(rel["dominated"])
        if not (a and b) or a.excluded or b.excluded:
            continue
        sa, sb = prices.strength(a.features, a.mana_value), prices.strength(b.features, b.mana_value)
        total += 1
        if sa >= sb - 1e-9:
            agree += 1
        else:
            worst.append((sb - sa, f"{rel['dominator']} ({sa:+.2f}) ≥ {rel['dominated']} ({sb:+.2f})"))
    worst.sort(reverse=True)
    return agree, total, [w for _, w in worst[:8]]


def _sp(body: str) -> list[str]:
    return [f"A:SP$ {body}"]


_UPKEEP = "T:Mode$ Phase | Phase$ Upkeep | ValidPlayer$ You | TriggerZones$ Battlefield | Execute$ U"
_TOKEN = "Token | TokenOwner$ You | TokenScript$ "

# (group, label, script lines, card type). Each effect is priced against the same minimal
# card without it: a 0/0 creature for bodies and keywords, a sorcery for one-shot effects,
# an artifact/enchantment for permanents.
UNIT_EFFECTS = [
    ("Corpo e palavras-chave", "+1 de poder", ["PT:1/0"], "Creature"),
    ("Corpo e palavras-chave", "+1 de resistência", ["PT:0/1"], "Creature"),
    *[("Corpo e palavras-chave", f"{kw} (numa 3/3)", ["PT:3/3", f"K:{kw}"], "Creature")
      for kw in ("Flying", "Trample", "Haste", "Vigilance", "Deathtouch", "Lifelink", "First Strike",
                 "Double Strike", "Menace", "Reach", "Hexproof", "Indestructible", "Ward:2", "Flash")],
    ("Corpo e palavras-chave", "Defender (custo, numa 3/3)", ["PT:3/3", "K:Defender"], "Creature"),
    ("Velocidade", "instantâneo em vez de feitiço", [], "Instant"),
    ("Cartas", "comprar 1 carta", _sp("Draw | Defined$ You | NumCards$ 1"), "Sorcery"),
    ("Cartas", "comprar 2 cartas", _sp("Draw | Defined$ You | NumCards$ 2"), "Sorcery"),
    ("Cartas", "comprar 3 cartas", _sp("Draw | Defined$ You | NumCards$ 3"), "Sorcery"),
    ("Cartas", "scry 2", _sp("Scry | ScryNum$ 2"), "Sorcery"),
    ("Cartas", "surveil 2", _sp("Surveil | Amount$ 2"), "Sorcery"),
    ("Cartas", "tutor: qualquer carta para a mão",
     _sp("ChangeZone | Origin$ Library | Destination$ Hand | ChangeType$ Card"), "Sorcery"),
    ("Cartas", "tutor: criatura para a mão",
     _sp("ChangeZone | Origin$ Library | Destination$ Hand | ChangeType$ Creature"), "Sorcery"),
    ("Cartas", "recursão: carta do cemitério para a mão",
     _sp("ChangeZone | Origin$ Graveyard | Destination$ Hand | ValidTgts$ Card.YouOwn"), "Sorcery"),
    ("Cartas", "reanimar criatura do seu cemitério",
     _sp("ChangeZone | Origin$ Graveyard | Destination$ Battlefield | ValidTgts$ Creature.YouOwn"), "Sorcery"),
    ("Cartas", "comprar 1 carta por turno (manutenção)",
     [_UPKEEP, "SVar:U:DB$ Draw | Defined$ You | NumCards$ 1"], "Enchantment"),
    ("Mana", "{T}: 1 mana incolor", ["A:AB$ Mana | Cost$ T | Produced$ C"], "Artifact"),
    ("Mana", "{T}: 1 mana de qualquer cor", ["A:AB$ Mana | Cost$ T | Produced$ Any"], "Artifact"),
    ("Mana", "terreno básico da biblioteca para o campo",
     _sp("ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic | ChangeNum$ 1"), "Sorcery"),
    ("Mana", "terreno básico da biblioteca para a mão",
     _sp("ChangeZone | Origin$ Library | Destination$ Hand | ChangeType$ Land.Basic | ChangeNum$ 1"), "Sorcery"),
    ("Mana", "jogar 1 terreno adicional por turno",
     ["S:Mode$ Continuous | Affected$ You | AdjustLandPlays$ 1"], "Enchantment"),
    ("Tokens", "criar 1 Treasure", _sp(_TOKEN + "c_a_treasure_sac"), "Sorcery"),
    ("Tokens", "criar 1 Clue", _sp(_TOKEN + "c_a_clue_draw"), "Sorcery"),
    ("Tokens", "criar 1 Food", _sp(_TOKEN + "c_a_food_sac"), "Sorcery"),
    ("Tokens", "criar 1 Blood", _sp(_TOKEN + "c_a_blood_draw"), "Sorcery"),
    ("Tokens", "criar token 1/1", _sp(_TOKEN + "w_1_1_soldier"), "Sorcery"),
    ("Tokens", "criar dois tokens 1/1", _sp("Token | TokenAmount$ 2 | TokenOwner$ You | TokenScript$ w_1_1_soldier"),
     "Sorcery"),
    ("Tokens", "criar token 2/2", _sp(_TOKEN + "g_2_2_bear"), "Sorcery"),
    ("Tokens", "criar token 3/3", _sp(_TOKEN + "g_3_3_beast"), "Sorcery"),
    ("Tokens", "criar token 4/4", _sp(_TOKEN + "g_4_4_beast"), "Sorcery"),
    ("Tokens", "token 1/1 por turno (manutenção)",
     [_UPKEEP, "SVar:U:DB$ Token | TokenOwner$ You | TokenScript$ w_1_1_soldier"], "Enchantment"),
    ("Remoção e interação", "destruir criatura", _sp("Destroy | ValidTgts$ Creature"), "Sorcery"),
    ("Remoção e interação", "destruir artefato", _sp("Destroy | ValidTgts$ Artifact"), "Sorcery"),
    ("Remoção e interação", "destruir encantamento", _sp("Destroy | ValidTgts$ Enchantment"), "Sorcery"),
    ("Remoção e interação", "destruir permanente", _sp("Destroy | ValidTgts$ Permanent"), "Sorcery"),
    ("Remoção e interação", "destruir terreno", _sp("Destroy | ValidTgts$ Land"), "Sorcery"),
    ("Remoção e interação", "exilar criatura",
     _sp("ChangeZone | Origin$ Battlefield | Destination$ Exile | ValidTgts$ Creature"), "Sorcery"),
    ("Remoção e interação", "exilar permanente não-terreno",
     _sp("ChangeZone | Origin$ Battlefield | Destination$ Exile | ValidTgts$ Permanent.nonLand"), "Sorcery"),
    ("Remoção e interação", "devolver criatura à mão",
     _sp("ChangeZone | Origin$ Battlefield | Destination$ Hand | ValidTgts$ Creature"), "Sorcery"),
    ("Remoção e interação", "−3/−3 até o fim do turno", _sp("Pump | ValidTgts$ Creature | NumAtt$ -3 | NumDef$ -3"),
     "Sorcery"),
    ("Remoção e interação", "virar criatura", _sp("Tap | ValidTgts$ Creature"), "Sorcery"),
    ("Remoção e interação", "anular mágica", _sp("Counter | TargetType$ Spell | ValidTgts$ Card"), "Sorcery"),
    ("Remoção e interação", "destruir todas as criaturas", _sp("DestroyAll | ValidCards$ Creature"), "Sorcery"),
    ("Remoção e interação", "cada oponente sacrifica uma criatura",
     _sp("Sacrifice | Defined$ Player.Opponent | SacValid$ Creature"), "Sorcery"),
    ("Remoção e interação", "oponente alvo descarta 1",
     _sp("Discard | ValidTgts$ Opponent | NumCards$ 1 | Mode$ TgtChoose"), "Sorcery"),
    ("Remoção e interação", "cada oponente descarta 1",
     _sp("Discard | Defined$ Player.Opponent | NumCards$ 1 | Mode$ TgtChoose"), "Sorcery"),
    ("Remoção e interação", "oponente alvo mói 3", _sp("Mill | ValidTgts$ Opponent | NumCards$ 3"), "Sorcery"),
    ("Dano e vida", "1 de dano em qualquer alvo", _sp("DealDamage | ValidTgts$ Any | NumDmg$ 1"), "Sorcery"),
    ("Dano e vida", "3 de dano em qualquer alvo", _sp("DealDamage | ValidTgts$ Any | NumDmg$ 3"), "Sorcery"),
    ("Dano e vida", "5 de dano em qualquer alvo", _sp("DealDamage | ValidTgts$ Any | NumDmg$ 5"), "Sorcery"),
    ("Dano e vida", "3 de dano em criatura", _sp("DealDamage | ValidTgts$ Creature | NumDmg$ 3"), "Sorcery"),
    ("Dano e vida", "3 de dano em jogador", _sp("DealDamage | ValidTgts$ Player | NumDmg$ 3"), "Sorcery"),
    ("Dano e vida", "2 de dano em cada oponente", _sp("DealDamage | Defined$ Player.Opponent | NumDmg$ 2"), "Sorcery"),
    ("Dano e vida", "cada oponente perde 2 de vida", _sp("LoseLife | Defined$ Player.Opponent | LifeAmount$ 2"),
     "Sorcery"),
    ("Dano e vida", "2 de dano em cada criatura", _sp("DamageAll | ValidCards$ Creature | NumDmg$ 2"), "Sorcery"),
    ("Dano e vida", "ganhar 1 de vida", _sp("GainLife | Defined$ You | LifeAmount$ 1"), "Sorcery"),
    ("Dano e vida", "ganhar 3 de vida", _sp("GainLife | Defined$ You | LifeAmount$ 3"), "Sorcery"),
    ("Dano e vida", "ganhar 5 de vida", _sp("GainLife | Defined$ You | LifeAmount$ 5"), "Sorcery"),
    ("Combate", "+2/+2 até o fim do turno", _sp("Pump | ValidTgts$ Creature | NumAtt$ +2 | NumDef$ +2"), "Sorcery"),
    ("Combate", "1 marcador +1/+1", _sp("PutCounter | ValidTgts$ Creature | CounterType$ P1P1 | CounterNum$ 1"),
     "Sorcery"),
    ("Combate", "+1/+1 para suas criaturas (estático)",
     ["S:Mode$ Continuous | Affected$ Creature.YouCtrl | AddPower$ 1 | AddToughness$ 1"], "Enchantment"),
    ("Combate", "suas criaturas ganham Flying (estático)",
     ["S:Mode$ Continuous | Affected$ Creature.YouCtrl | AddKeyword$ Flying"], "Enchantment"),
    ("Combate", "+2/+2 até o fim do turno para suas criaturas",
     _sp("PumpAll | ValidCards$ Creature.YouCtrl | NumAtt$ +2 | NumDef$ +2"), "Sorcery"),
]


def effect_prices(prices) -> list[str]:
    """Mana price of canonical effects (π summed over each effect's back-off chain), in
    the design (1 vs 1) and under Commander rules."""
    from operators.compile import compile_script
    from operators.pricing import NUISANCE

    def value(lines: list[str], typ: str, commander: bool) -> float:
        script = "\n".join(["Name:Unit", "ManaCost:1", f"Types:{typ}", *lines])
        vec = card_vector(compile_script(script))
        features = commander_features(vec) if commander else vec.features
        return prices.design_row({k: v for k, v in features.items()
                                  if not k.startswith("type|") and k not in NUISANCE})

    rows, group = [], ""
    for grp, label, lines, typ in UNIT_EFFECTS:
        if typ == "Creature":  # same body without the effect
            body = [x for x in lines if x.startswith("PT:")]
            base_lines, base_typ = (["PT:0/0"] if label.startswith("+1") else body), typ
        elif typ == "Instant":
            base_lines, base_typ = [], "Sorcery"
        else:
            base_lines, base_typ = [], typ
        design = value(lines, typ, False) - value(base_lines, base_typ, False)
        edh = value(lines, typ, True) - value(base_lines, base_typ, True)
        if grp != group:
            rows.append(f"| **{grp}** | | |")
            group = grp
        rows.append(f"| {label} | {design:+.2f} | {edh:+.2f} |")
    return rows


def simple_creature_keywords(vecs, prices) -> list[str]:
    """Second estimate for keywords: unconstrained least squares on creatures that have
    nothing but a body and keywords, where nothing else can absorb the keyword's price."""
    simple = [v for v in vecs if "Creature" in v.types and not any(
        not k.startswith(("type|", "color|", "cost|", "body|", "kw|")) for k in v.features)]
    counts: dict[str, int] = {}
    for v in simple:
        for k in v.features:
            if k.startswith("kw|"):
                counts[k] = counts.get(k, 0) + 1
    keys = ["body|power", "body|toughness"] + sorted(k for k, n in counts.items() if n >= 15)
    X = np.array([[1.0] + [v.features.get(k, 0.0) for k in keys] for v in simple])
    y = np.array([v.mana_value for v in simple], dtype=float)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    idx = {f: i for i, f in enumerate(prices.features)}
    rows = [f"| `{k}` | {prices.weights[idx[k]] * (prices.signs.get(k, 1) or 1):+.2f} | {b:+.2f} | "
            f"{counts.get(k, len(simple))} |"
            for k, b in sorted(zip(keys, beta[1:]), key=lambda kv: -kv[1]) if k in idx]
    return [f"{len(simple)} criaturas só com corpo e palavras-chave.", "",
            "| feature | modelo completo | criaturas simples | cartas |", "|---|---:|---:|---:|", *rows]


def sensitivity(vecs, groups, byname, folds: int) -> list[str]:
    """How the floor on instant speed / Flash moves the fit and the ranking."""
    from scipy.stats import spearmanr

    floors = (0.0, 0.15, 0.3, 0.5)
    fits, rows = {}, []
    for f in floors:
        fl = {"speed|instant": f, "kw|Flash": f}
        fits[f] = fit(vecs, groups, floors=fl)
        cv = cross_validate(vecs, groups, folds, floors=fl)
        fits[f].cv = cv["mae"]
    ref = fits[0.15]
    complete = [v for v in vecs if ref.complete(v)]
    s_ref = [ref.strength(v.features, v.mana_value) for v in complete]
    pairs = [("Quick Study", "Divination"), ("Murder", "Eviscerate"), ("Lightning Bolt", "Chain Lightning")]
    for f in floors:
        p = fits[f]
        rho = spearmanr(s_ref, [p.strength(v.features, v.mana_value) for v in complete]).statistic
        cells = []
        for a, b in pairs:
            va, vb = byname.get(a), byname.get(b)
            if va and vb and not (va.excluded or vb.excluded):
                cells.append(f"{p.strength(va.features, va.mana_value) - p.strength(vb.features, vb.mana_value):+.2f}")
            else:
                cells.append("—")
        idx = {k: i for i, k in enumerate(p.features)}
        rows.append(f"| {f:.2f} | {p.weights[idx['speed|instant']]:.2f} | {p.weights[idx['kw|Flash']]:.2f} | "
                    f"{p.cv:.3f} | {rho:.4f} | " + " | ".join(cells) + " |")
    header = ["| piso | π instantâneo | π Flash | erro fora da amostra | Spearman da força vs piso 0,15 | "
              + " | ".join(f"s({a}) − s({b})" for a, b in pairs) + " |",
              "|---:|---:|---:|---:|---:|" + "---:|" * len(pairs)]
    return header + rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bootstrap", type=int, default=30)
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    t0 = time.time()
    DAMAGE.update(combat_keyword_damage())
    cards = load_cards()
    first = first_printings(EDITIONS)
    vecs, groups, byname, excluded = [], [], {}, {}
    for card in cards:
        vec = card_vector(card)
        printed = first.get(card.name.lower())
        if not vec.excluded and not printed:
            vec.excluded = "sem data de impressão"
        byname[card.name] = vec
        if vec.excluded:
            reason = vec.excluded.split(" (")[0]
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        vecs.append(vec)
        groups.append(era_group(*printed))
    print(f"{len(cards)} cartas, {len(vecs)} no ajuste ({time.time() - t0:.0f}s)")

    cv = cross_validate(vecs, groups, args.folds)
    print(f"MAE fora da amostra {cv['mae']:.3f} vs baseline {cv['baseline']:.3f}")
    prices = fit(vecs, groups)
    shown = [n for n in SHOWCASE if n in byname and not byname[n].excluded]
    pi_ci, s_ci = bootstrap(vecs, groups, args.bootstrap, shown, byname)
    agree, total, disagreements = dominance_check(prices, byname)
    save_prices(prices, PRICES_PATH)

    complete = [v for v in vecs if prices.complete(v)]
    ranked = sorted(((prices.strength(v.features, v.mana_value), v.name, v.mana_value) for v in complete))
    ok_cv = cv["mae"] < cv["baseline"]
    eye = s_ci.get("Mind's Eye", (0.0, 0.0))
    ok_eye = eye[1] < 0
    ok_dom = total > 0 and agree / total >= 0.95
    verdict = "ACEITE" if ok_cv and ok_eye and ok_dom else "NÃO ACEITE"

    order = np.argsort(-prices.weights)
    unit_rows = effect_prices(prices)
    sens_rows = sensitivity(vecs, groups, byname, args.folds)
    kw_rows = simple_creature_keywords(vecs, prices)
    key_features = ["body|power", "body|toughness", "body|big", "kw|Flying", "kw|Trample", "kw|Haste", "kw|Deathtouch",
                    "kw|Lifelink", "kw|Flash", "kw|Defender", "once|Draw|you", "turn|Draw|you", "once|DealDamage|tgt_Any",
                    "once|Destroy|tgt_Creature", "once|Destroy|tgt_Permanent", "once|Counter|tgt_Card",
                    "once|GainLife|you", "act|Mana|you", "once|ChangeZone.Library>Battlefield|all_Land",
                    "once|Token|pt", "static|pt|yours", "cost|colored_pips"]
    idx = {f: i for i, f in enumerate(prices.features)}

    def pi_row(f: str) -> str:
        lo, hi = pi_ci.get(f, (float("nan"), float("nan")))
        sign = {1: "efeito", -1: "custo", 0: "livre"}[prices.signs.get(f, 1)]
        return f"| `{f}` | {prices.weights[idx[f]]:.3f} | [{lo:.3f}, {hi:.3f}] | {sign} | {prices.support[f]} |"

    def card_row(name: str) -> str:
        vec = byname[name]
        s = prices.strength(vec.features, vec.mana_value)
        s_edh = prices.strength(commander_features(vec), vec.mana_value)
        lo, hi = s_ci.get(name, (float("nan"), float("nan")))
        return f"| {name} | {vec.mana_value} | {s:+.2f} | [{lo:+.2f}, {hi:+.2f}] | {s_edh:+.2f} | {'' if prices.complete(vec) else 'parcial'} |"

    risers = sorted(((prices.strength(commander_features(v), v.mana_value) - prices.strength(v.features, v.mana_value),
                      v.name) for v in complete), reverse=True)[:12]
    eras = sorted((k, v) for k, v in prices.intercepts.items() if k.startswith("era|"))
    lines = [
        "# Preços latentes e força de carta (Fase 4)",
        "",
        "Gerado por `scripts/fit_prices.py`. Fonte: scripts do Forge compilados em operadores",
        "(Fase 2); nenhuma popularidade. Modelo: `mana_value ≈ b_0 + b_era|produto + π·(E − C)`,",
        "Huber, π ≥ 0 nas features com sinal conhecido, mana como numerário (seção 3.1).",
        "",
        f"**Veredito:** {verdict}.",
        "",
        f"Cartas legais compiladas: {len(cards)}; no ajuste: {len(vecs)}; features com preço: "
        f"{len(prices.features)} (suporte ≥ 10 cartas). Fora do ajuste: "
        + ", ".join(f"{k} {v}" for k, v in sorted(excluded.items(), key=lambda kv: -kv[1])) + ".",
        "",
        f"## 1. Mana value fora da amostra — {'ok' if ok_cv else 'falhou'}",
        "",
        f"{args.folds} dobras. Baseline: média do mana value da mesma linha de tipo no treino.",
        "",
        "| faixa de mana value | cartas | erro absoluto médio (π) | baseline |",
        "|---|---:|---:|---:|",
        *[f"| {label} | {n} | {m:.2f} | {b:.2f} |" for label, n, m, b in cv["buckets"]],
        f"| **todas** | {len(vecs)} | **{cv['mae']:.3f}** | {cv['baseline']:.3f} |",
        "",
        f"RMSE {cv['rmse']:.3f} contra {cv['baseline_rmse']:.3f}. Erro {1 - cv['mae'] / cv['baseline']:.0%} menor que a baseline.",
        "",
        f"## 2. Mind's Eye — {'ok' if ok_eye else 'falhou'}",
        "",
        "Força `s_k` = mana value que os efeitos valem − mana value pago, centrada nas cartas do mesmo",
        "custo (prever custo a partir de efeitos regride à média; a centralização tira esse viés) e",
        f"absoluta (sem o efeito de era). Intervalo: percentis 5–95 de {args.bootstrap} reamostras do bootstrap",
        "bayesiano (pesos Dirichlet sobre as cartas).",
        "`s_edh`: os mesmos π com os efeitos sob as regras de Commander (\"cada oponente\" × 3,",
        "gatilhos em ações dos oponentes × 3, vida vale metade com 40).",
        "",
        "| carta | mana value | s_k | intervalo 90% | s_edh | |",
        "|---|---:|---:|---|---:|---|",
        *[card_row(n) for n in shown],
        "",
        f"## 3. Consistência com a dominância (Fase 2) — {'ok' if ok_dom else 'falhou'}",
        "",
        f"Pares em que uma carta domina a outra (mesmos efeitos, nada pior): o dominante tem s_k ≥ o",
        f"dominado em **{agree} de {total}** ({agree / max(1, total):.1%}).",
        *([""] + [f"- discorda: {d}" for d in disagreements] if disagreements else []),
        "",
        "## Preços π",
        "",
        "Preço de cada efeito em mana: soma de π sobre a cadeia de back-off do efeito, medida num",
        "script mínimo do Forge contra a mesma carta sem o efeito (sem tipo nem pips). Coluna",
        "Commander: os mesmos π com as regras do formato (\"cada oponente\" × 3, vida × ½).",
        "São preços **marginais do design** (1 contra 1): a regressão os comprime em direção à",
        "média, então servem para comparar efeitos entre si, não como custo absoluto de uma carta.",
        "",
        "| efeito | design (mana) | Commander (mana) |",
        "|---|---:|---:|",
        *unit_rows,
        "",
        "### Positividade estrita e palavras-chave de combate",
        "",
        f"Todo efeito e todo custo têm preço ≥ ε = {EPSILON} por unidade (na chave mais grossa da",
        "cadeia e nas features isoladas): carta + qualquer benefício extra é estritamente mais forte.",
        "As palavras-chave de combate que o relógio simula têm, além disso, um termo por ponto de",
        "poder (`kw|X×power`) com piso derivado do relógio: dano extra até T10 de uma criatura p/p com",
        "a palavra-chave, por ponto de poder, em unidades de +1 de poder, vezes π_poder:",
        "",
        "| palavra-chave | dano extra por ponto de poder (em pontos de poder) |",
        "|---|---:|",
        *[f"| {flag} | {v:.3f} |" for flag, v in sorted(DAMAGE.items())],
        "",
        "### Palavras-chave: duas estimativas",
        "",
        "Sem pisos, o custo impresso **não identifica** palavras-chave de ~0,2 de mana: no modelo",
        "completo sem restrição de sinal o Trample sai −0,1; nas criaturas só com corpo e",
        "palavras-chave, +0,2 a +0,4. Por isso os pisos acima (ε e relógio) — a coluna \"modelo",
        "completo\" abaixo já é o preço final da parte fixa (sem o termo por poder).",
        "",
        *kw_rows,
        "",
        "### Sensibilidade ao piso de velocidade",
        "",
        "A curva de design quase não cobra mana por velocidade (Quick Study e Divination custam 3),",
        "mas um instantâneo faz tudo o que o feitiço faz e mais. Até a Fase 8 medir o valor de jogo",
        "da velocidade, `speed|instant` e `kw|Flash` têm um **piso normativo** (padrão 0,15). A",
        "tabela refaz o ajuste e a validação cruzada com cada piso:",
        "",
        *sens_rows,
        "",
        "Features individuais (a parte específica de cada uma; o resto do preço está nas chaves mais",
        "grossas da cadeia):",
        "",
        "| feature | π | intervalo 90% | sinal | cartas |",
        "|---|---:|---|---|---:|",
        *[pi_row(f) for f in key_features if f in idx],
        "",
        "Maiores preços:",
        "",
        "| feature | π | intervalo 90% | sinal | cartas |",
        "|---|---:|---|---|---:|",
        *[pi_row(prices.features[j]) for j in order[:20]],
        "",
        "## Efeito de era e produto",
        "",
        "Intercepto por época da primeira impressão (edições do Forge) e por produto (set comum vs",
        "produto de Commander). Negativo = os mesmos efeitos custam menos: power creep.",
        "",
        "| grupo | b |",
        "|---|---:|",
        *[f"| {k.removeprefix('era|')} | {v:+.2f} |" for k, v in eras],
        "",
        "## Commander: quem mais sobe",
        "",
        "| carta | Δs (Commander − design) |",
        "|---|---:|",
        *[f"| {name} | {d:+.2f} |" for d, name in risers],
        "",
        "## Extremos (cartas com todos os efeitos precificados)",
        "",
        "Mais fortes: " + ", ".join(f"{n} ({mv}, {s:+.1f})" for s, n, mv in ranked[-15:][::-1]) + ".",
        "",
        "Mais fracas: " + ", ".join(f"{n} ({mv}, {s:+.1f})" for s, n, mv in ranked[:15]) + ".",
        "",
        "Leitura: o topo mistura corpos grandes de verdade acima da curva (Gigantosaurus, Progenitus)",
        "com **lacunas do extrator** — drawbacks que a abstração ainda não lê (P/T variável negativo do",
        "Death's Shadow, sacrifício condicional do Phyrexian Dreadnought, marcadores de sono do",
        "Arixmethes). O fundo é dominado por cartas caras de efeito complexo que as features não",
        "capturam por inteiro (turnos extras, cópias, efeitos únicos). O desvio de s_k cresce com o",
        "custo; para cartas de 7+ a força é pouco informativa.",
        "",
        "## O que π não mede: tempo",
        "",
        "π e s_k são **estáticos**: medem quanto efeito a carta entrega pelo mana, como se o turno",
        "não importasse. No jogo o custo de mana é também **a partir de que turno a carta age**, e",
        "o valor do efeito depende do estado da mesa nesse turno: Rhystic Study é ótima no T2, quando",
        "os oponentes não têm mana sobrando para a taxa, e fraca no T10. Esse valor no tempo,",
        "`v_k(t) = Σ_u γ^u · π · e_k(u | s_u)`, é da Fase 5 (`V(D)` com o relógio); aqui π é o",
        "preço por unidade de efeito que ela usa.",
        "",
        "## Limites",
        "",
        "- π herda o julgamento de design da WotC (seção 3.1): é um prior, não a verdade; as âncoras",
        "  terminal (relógio) e de oráculo entram nas Fases 5 e 8.",
        "- Linear e aditivo: interações dentro da carta (voar num 5/5 vale mais que num 1/1) só",
        "  aparecem pelo termo `body|big`.",
        "- Ativadas são descontadas por `1/(1 + custo de ativação)`, uma escolha de modelagem.",
        "- Cartas com custo X, custo alternativo, várias faces ou terrenos ficam fora do ajuste.",
        f"- Tempo: {time.time() - t0:.0f}s com {args.bootstrap} reamostras.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{verdict} (cv {ok_cv}, mind's eye {ok_eye} {eye}, dominância {agree}/{total}) -> {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
