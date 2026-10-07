# Relógio de dano (Fase 2.5)

Gerado por `scripts/clock_report.py`. Cartas reais compiladas dos scripts do Forge;
300 ordens de compra por deck, horizonte T12, oponentes como estoque de
bloqueadores (0.75/turno a partir do T2; bloqueio chump gasta o bloqueador).
Vitória: 120 de vida tirada (combate, dano direto e drenos somam), 30 de veneno, 63 de dano
de comandante, 255 cartas moídas (3 oponentes) ou vitória alternativa / "oponentes perdem"
com a condição do script avaliada no estado. Goldfish abstrato: os oponentes não jogam, só
bloqueiam e nos tiram 3 de vida por turno a partir do T3.

**Veredito:** ACEITE.

## 1. Ramp × topo de curva — ok

| mão inicial (5 Forests +) | dano até T10 |
|---|---:|
| nenhum | 0 |
| só 7/7 | 21 |
| só 2 ramps | 0 |
| 7/7 + 2 ramps | 35 |

syn(ramp, 7/7) = +14 de dano = **2.0 ataques a mais** do Pelakka Wurm (7/7 trample, 7 manas), sem nenhuma palavra em comum com Rampant Growth. Ordem de compra fixa, sem bloqueadores, para isolar o efeito descrito no plano.

## 2. Overrun cresce com o número de criaturas — ok

| criaturas no deck | dano T8 sem Overrun | com Overrun | ganho |
|---:|---:|---:|---:|
| 5 | 0.7 | 0.9 | +0.23 |
| 15 | 6.8 | 7.4 | +0.60 |
| 25 | 14.8 | 15.8 | +1.02 |
| 35 | 23.2 | 24.7 | +1.48 |

Ganho médio por partida (o Overrun é 1 carta em 99, comprado até T8 em ~15% delas).

## 3. Stomp vs trocar ameaças por compra — ok

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| stomp verde | T12.63 | 0.64 | 78% | 9.7 | 28.7 | 87 / 0 | 0% | 3.29 | none 78%, damage 22% |
| 10 mais fortes → 10 draw | T12.82 | 0.36 | 90% | 6.6 | 23.4 | 72 / 0 | 0% | 3.80 | none 90%, damage 10% |

Deck: 37 Forests, 12 ramp (Llanowar Elves, Elvish Mystic, Fyndhorn Elves, Rampant Growth, Cultivate, Kodama's Reach, Wood Elves, Farhaven Elf, Nature's Lore, Three Visits, Sol Ring, Arcane Signet), 30 criaturas mono-verdes sem habilidades que o relógio lê (6 por mana value 2–6, ordem alfabética do catálogo), finalizadores (Overrun, Craterhoof Behemoth, Overwhelming Stampede), 15 mágicas verdes sem efeito no relógio. Trocadas: Aboroth, Aggressive Mammoth, Adaptive Snapjaw, Alpha Tyrranax, Ambush Krotiq, Ancient Greenwarden, Afterburner Expert, Ainok Artillerist, Agatha's Champion, Ageless Entity. Draw: Harmonize, Divination, Concentrate, Tidings, Inspiration, Opportunity, Sign in Blood, Night's Whisper, Phyrexian Arena, Read the Bones.

### Achado: a primeira versão deste teste

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| lista de ameaças à mão | T11.64 | 1.60 | 31% | 17.1 | 46.7 | 113 / 0 | 0% | 2.27 | damage 69%, none 31% |
| 10 primeiras → 10 draw | T11.70 | 1.51 | 34% | 15.5 | 46.4 | 112 / 0 | 0% | 2.43 | damage 66%, none 34% |

Trocadas: Colossal Dreadmaw (6), Pelakka Wurm (7), Craw Wurm (6), Krosan Colossus (9), Thragtusk (5), Garruk's Packleader (5), Primeval Titan (6), Terastodon (8), Woodfall Primus (8), Ancient Brontodon (8). Várias custam 8–9 manas e quase não entram até T12; o modelo diz, corretamente, que compra barata vale mais que elas nesse horizonte. Por isso o critério 3 acima troca as mais fortes **lançáveis**.

## 4. Rotas além do combate — ok

Decks de 99 com cartas reais; o resto é `Cancel` (nenhum efeito que o relógio lê), para cada
cenário mostrar a própria rota. Horizonte T12 (T15 onde indicado), 300 ordens de compra.

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| aristocrats | T12.97 | 0.06 | 99% | 6.9 | 15.1 | 20 / 16 | 1% | 3.55 | none 99%, damage 1% |
| aristocrats sem payoffs de morte | T13.00 | 0.00 | 100% | 2.7 | 5.6 | 13 / 2 | 0% | 4.09 | none 100% |
| spellslinger | T13.00 | 0.00 | 100% | 11.7 | 16.9 | 8 / 21 | 0% | 4.57 | none 100% |
| spellslinger sem payoffs | T13.00 | 0.00 | 100% | 3.3 | 3.8 | 0 / 5 | 0% | 4.92 | none 100% |
| Maze's End + 15 Gates (T15) | T15.37 | 2.63 | 86% | 0.0 | 0.0 | 0 / 0 | 0% | 4.95 | none 86%, alt_win 14% |
| Maze's End + 3 Gates (T15) | T16.00 | 0.00 | 100% | 0.0 | 0.0 | 0 / 0 | 0% | 5.47 | none 100% |
| mill | T13.00 | 0.00 | 100% | 0.0 | 0.0 | 0 / 0 | 11% | 4.33 | none 100% |
| self-mill + Oracle/Maniac/Jace (T15) | T16.00 | 0.00 | 100% | 0.1 | 0.1 | 0 / 0 | 7% | 4.85 | none 100% |
| self-mill sem as vitórias (T15) | T16.00 | 0.00 | 100% | 0.1 | 0.1 | 0 / 0 | 15% | 4.94 | none 100% |
| Sanguine Bond + Exquisite Blood | T12.73 | 1.08 | 92% | 1.9 | 4.7 | 4 / 13 | 1% | 3.79 | none 92%, damage 8% |
| só Sanguine Bond | T13.00 | 0.00 | 100% | 1.1 | 2.7 | 4 / 4 | 1% | 4.02 | none 100% |

- ok: aristocrats: os payoffs de morte multiplicam o dano (≥ 2×) e a maior parte vem de fora do combate (≥ 40%)
- ok: spellslinger: os payoffs transformam mágicas em dano direto (direto > combate, e mais dano que sem eles)
- ok: Maze's End vence por vitória alternativa com ≥ 10 Gates de nomes diferentes, e nunca com 3
- ok: mill: o progresso vem da biblioteca dos oponentes, não da vida
- ok: self-mill: com Oracle/Maniac/Jace no deck, os "jogador-alvo mói N" passam a moer a própria biblioteca
- ok: loop Sanguine Bond + Exquisite Blood fecha o jogo pelo dano direto (e só o Bond não)

Leitura: as rotas fora do combate são lentas em goldfish de 99 cartas com uma cópia de cada
peça (o Maze's End é 1 carta em 99 e não há tutores aqui); o que o critério mede é a direção
(a peça certa acelera a rota certa), não que o arquétipo vença sozinho até T12. O self-mill
sem cartas que moem a biblioteca inteira (Hermit Druid etc., ainda não lidas) não esvazia 85
cartas até T15; as vitórias de Oracle/Maniac/Jace estão cobertas por testes determinísticos
(`tests/test_clock.py`).

### Cobertura das vitórias alternativas

Cartas legais em Commander com `WinsGame`/`LosesGame` no script: **84**. O relógio avalia a condição de **28** (33%) no estado simulado; as outras 56 ficam sem promessa (condição que o avaliador não lê, ou que depende do oponente).

- Lidas: Approach of the Second Sun, Azor's Elocutors, Battle of Wits, Biovisionary, Call the Spirit Dragons, Chance Encounter, Coalition Victory, Doctor Doom, Unrivaled, Epic Struggle, Felidar Sovereign, Halo Fountain, Happily Ever After, Hedron Alignment, Helix Pinnacle, Hellkite Tyrant, Jace, Wielder of Mysteries, Knuckles the Echidna, Laboratory Maniac, Luck Bobblehead, Maze's End, Mechanized Production, Mortal Combat, Near-Death Experience, Revel in Riches, Simic Ascendancy, Test of Endurance, Thassa's Oracle, Triskaidekaphile.
- Não lidas: Alchemist's Gambit, Angel of Destiny, Archfiend of the Dross, Atemsis, All-Seeing, Barren Glory, Blood Tyrant, Celestial Convergence, Central Elevator, Chance for Glory, Curse of Vengeance, Darksteel Reactor, Demonic Pact, Door to Nothingness, Elbrus, the Binding Blade, Enduring Angel, Etrata, the Silencer, Ezio Auditore da Firenze, Faithbound Judge, Final Fortune, Forbidden Crypt, Frodo, Sauron's Bane, Gallifrey Stands, Glorious End, Hidetsugu Consumes All, Immortal Coil, Intervention Pact, Last Chance, Lich, Lich's Mastery, Liliana's Contract, Marina Vendrell's Grimoire, Mayael's Aria, Mirrodin Besieged, Nefarious Lich, Nicol Bolas, Dragon-God, Nine Lives, Out of the Tombs, Pact of Negation, Pact of the Titan, Phage the Untouchable, Ramses, Assassin Lord, Sengir, the Dark Baron, Share the Spoils, Slaughter Pact, Strixhaven Stadium, Summon: Primal Odin, Summoner's Pact, The Deck of Many Things, Transcendence, Triskaidekaphobia, Twenty-Toed Toad, Vorpal Sword, Vraska, Golgari Queen, Vraska, Scheming Gorgon, Warrior's Oath, Zenos yae Galvus.

## Sensibilidade aos parâmetros do oponente

`P_conectar` e a capacidade de bloqueio são parâmetros até a Fase 8 (risco do plano).
A conclusão do critério 3 precisa valer na faixa toda:

| taxa de bloqueadores/turno | P_conectar (evasão) | dano T8 stomp | dano T8 trocado | stomp melhor? |
|---:|---:|---:|---:|:---:|
| 0.25 | 0.7 | 41.1 | 33.4 | sim |
| 0.25 | 0.85 | 41.1 | 33.3 | sim |
| 0.25 | 1.0 | 41.1 | 33.5 | sim |
| 0.75 | 0.7 | 27.0 | 22.6 | sim |
| 0.75 | 0.85 | 27.0 | 22.5 | sim |
| 0.75 | 1.0 | 27.1 | 22.8 | sim |
| 1.25 | 0.7 | 16.9 | 14.3 | sim |
| 1.25 | 0.85 | 17.1 | 14.3 | sim |
| 1.25 | 1.0 | 17.2 | 14.6 | sim |

## Decks do repositório

Decks gerados pelo sistema antigo (`data/`) e decks humanos (`lucas_plans/Decks/`).
Todas as rotas contam, mas em goldfish: um deck de controle aparece lento aqui por
construção (remoção e interação só ganham valor com oponentes que jogam, Fase 8).

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| deck_ertai_resurrected (gerado; comandante: Ertai Resurrected) | T12.91 | 0.10 | 92% | 8.5 | 25.5 | 80 / 0 | 0% | 3.11 | none 92%, damage 8% |
| deck_krenko_mob_boss (gerado; comandante: Krenko, Mob Boss) | T9.93 | 1.55 | 8% | 6.6 | 52.0 | 114 / 0 | 0% | 1.41 | damage 92%, none 8% |
| deck_lightning_army_of_one (gerado; comandante: Lightning, Army of One) | T12.97 | 0.03 | 98% | 6.4 | 16.7 | 49 / 0 | 0% | 3.51 | none 98%, damage 2%, commander 0% |
| deck_marina_vendrell (gerado; comandante: Marina Vendrell) | T12.99 | 0.01 | 99% | 3.1 | 10.8 | 37 / 0 | 0% | 3.39 | none 99%, damage 1% |
| hope (humano; comandante: Hope Estheim) | T12.59 | 1.02 | 81% | 5.5 | 12.6 | 34 / 0 | 0% | 4.02 | none 81%, alt_win 18%, damage 1% |
| Ivy (humano; comandante: Ivy, Gleeful Spellthief) | T12.89 | 0.18 | 93% | 16.7 | 31.6 | 72 / 0 | 0% | 3.83 | none 93%, damage 7% |
| Karrthus (humano; comandante: Karrthus, Tyrant of Jund) | T11.26 | 1.58 | 23% | 15.0 | 46.6 | 112 / 0 | 0% | 2.08 | damage 77%, none 23% |
| kotis (humano; comandante: Kotis, the Fangkeeper) | T12.67 | 0.39 | 75% | 10.0 | 28.6 | 86 / 0 | 0% | 3.21 | none 75%, damage 25% |
| lightining (humano; comandante: Lightning, Army of One) | T12.98 | 0.02 | 98% | 11.5 | 28.1 | 71 / 0 | 0% | 3.94 | none 98%, damage 2%, commander 0% |
| livaan (humano; comandante: Livaan, Cultist of Tiamat) | T12.74 | 0.30 | 79% | 7.7 | 25.9 | 87 / 0 | 0% | 3.13 | none 79%, damage 21% |
| Rebento de alara (humano; comandante: Child of Alara) | T12.92 | 0.09 | 93% | 6.9 | 23.2 | 73 / 5 | 0% | 2.43 | none 93%, damage 5%, alt_win 3% |
| saheeli (humano; comandante: Saheeli, Sublime Artificer) | T12.67 | 0.44 | 77% | 7.2 | 24.6 | 85 / 0 | 0% | 3.73 | none 77%, damage 23% |
| teval (humano; comandante: Teval, the Balanced Scale) | T12.77 | 0.35 | 85% | 15.9 | 35.7 | 86 / 0 | 1% | 4.36 | none 85%, damage 15% |

- `kotis.txt`: sem script no Forge: Repudiate/Replicate
