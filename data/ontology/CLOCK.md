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
| 5 | 1.6 | 1.8 | +0.22 |
| 15 | 9.6 | 10.1 | +0.54 |
| 25 | 18.4 | 19.3 | +0.90 |
| 35 | 27.1 | 28.4 | +1.31 |

Ganho médio por partida (o Overrun é 1 carta em 99, comprado até T8 em ~15% delas).

## 3. Stomp vs trocar ameaças por compra — ok

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| stomp verde | T12.60 | 0.71 | 77% | 10.9 | 28.6 | 87 / 0 | 0% | 3.28 | none 77%, damage 23% |
| 10 mais fortes → 10 draw | T12.83 | 0.33 | 90% | 7.5 | 23.4 | 72 / 0 | 0% | 3.79 | none 90%, damage 10% |

Deck: 37 Forests, 12 ramp (Llanowar Elves, Elvish Mystic, Fyndhorn Elves, Rampant Growth, Cultivate, Kodama's Reach, Wood Elves, Farhaven Elf, Nature's Lore, Three Visits, Sol Ring, Arcane Signet), 30 criaturas mono-verdes sem habilidades que o relógio lê (6 por mana value 2–6, ordem alfabética do catálogo), finalizadores (Overrun, Craterhoof Behemoth, Overwhelming Stampede), 15 mágicas verdes sem efeito no relógio. Trocadas: Aboroth, Aggressive Mammoth, Adaptive Snapjaw, Alpha Tyrranax, Ambush Krotiq, Ancient Greenwarden, Afterburner Expert, Ainok Artillerist, Agatha's Champion, Ageless Entity. Draw: Harmonize, Divination, Concentrate, Tidings, Inspiration, Opportunity, Sign in Blood, Night's Whisper, Phyrexian Arena, Read the Bones.

### Achado: a primeira versão deste teste

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| lista de ameaças à mão | T11.52 | 1.67 | 28% | 20.5 | 49.8 | 115 / 0 | 0% | 2.22 | damage 72%, none 28% |
| 10 primeiras → 10 draw | T11.60 | 1.55 | 31% | 18.8 | 49.2 | 114 / 0 | 0% | 2.38 | damage 69%, none 31% |

Trocadas: Colossal Dreadmaw (6), Pelakka Wurm (7), Craw Wurm (6), Krosan Colossus (9), Thragtusk (5), Garruk's Packleader (5), Primeval Titan (6), Terastodon (8), Woodfall Primus (8), Ancient Brontodon (8). Várias custam 8–9 manas e quase não entram até T12; o modelo diz, corretamente, que compra barata vale mais que elas nesse horizonte. Por isso o critério 3 acima troca as mais fortes **lançáveis**.

## 4. Rotas além do combate — ok

Decks de 99 com cartas reais; o resto é `Cancel` (nenhum efeito que o relógio lê), para cada
cenário mostrar a própria rota. Horizonte T12 (T15 onde indicado), 300 ordens de compra.

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| aristocrats | T12.98 | 0.06 | 99% | 6.3 | 13.2 | 16 / 15 | 1% | 3.55 | none 99%, damage 1% |
| aristocrats sem payoffs de morte | T13.00 | 0.00 | 100% | 2.0 | 3.8 | 7 / 2 | 0% | 4.09 | none 100% |
| spellslinger | T13.00 | 0.00 | 100% | 12.1 | 17.7 | 10 / 21 | 0% | 4.57 | none 100%, damage 0% |
| spellslinger sem payoffs | T13.00 | 0.00 | 100% | 3.3 | 3.8 | 0 / 5 | 0% | 4.92 | none 100% |
| Maze's End + 15 Gates (T15) | T15.37 | 2.63 | 86% | 0.0 | 0.0 | 0 / 0 | 0% | 4.95 | none 86%, alt_win 14% |
| Maze's End + 3 Gates (T15) | T16.00 | 0.00 | 100% | 0.0 | 0.0 | 0 / 0 | 0% | 5.47 | none 100% |
| mill | T13.00 | 0.00 | 100% | 0.0 | 0.0 | 0 / 0 | 11% | 4.33 | none 100% |
| self-mill + Oracle/Maniac/Jace (T15) | T16.00 | 0.00 | 100% | 0.1 | 0.2 | 1 / 0 | 7% | 4.85 | none 100% |
| self-mill sem as vitórias (T15) | T16.00 | 0.00 | 100% | 0.1 | 0.1 | 0 / 0 | 15% | 4.94 | none 100% |
| Sanguine Bond + Exquisite Blood | T12.69 | 1.19 | 91% | 1.9 | 4.6 | 4 / 13 | 1% | 3.78 | none 91%, damage 9% |
| só Sanguine Bond | T13.00 | 0.00 | 100% | 1.1 | 2.6 | 5 / 3 | 1% | 4.02 | none 100% |

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
| 0.25 | 0.7 | 41.6 | 33.8 | sim |
| 0.25 | 0.85 | 41.8 | 33.9 | sim |
| 0.25 | 1.0 | 41.9 | 34.1 | sim |
| 0.75 | 0.7 | 26.8 | 22.8 | sim |
| 0.75 | 0.85 | 27.0 | 22.8 | sim |
| 0.75 | 1.0 | 27.1 | 23.0 | sim |
| 1.25 | 0.7 | 18.4 | 16.2 | sim |
| 1.25 | 0.85 | 18.6 | 16.2 | sim |
| 1.25 | 1.0 | 18.7 | 16.5 | sim |

## Decks do repositório

Decks gerados pelo sistema antigo (`data/`) e decks humanos (`lucas_plans/Decks/`).
Todas as rotas contam, mas em goldfish: um deck de controle aparece lento aqui por
construção (remoção e interação só ganham valor com oponentes que jogam, Fase 8).

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | vida tirada até T: combate / direto | mill até T | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| deck_ertai_resurrected (gerado; comandante: Ertai Resurrected) | T12.94 | 0.07 | 94% | 9.7 | 25.0 | 78 / 0 | 0% | 3.11 | none 94%, damage 6% |
| deck_krenko_mob_boss (gerado; comandante: Krenko, Mob Boss) | T9.92 | 1.54 | 8% | 6.7 | 50.8 | 114 / 0 | 0% | 1.41 | damage 92%, none 8% |
| deck_lightning_army_of_one (gerado; comandante: Lightning, Army of One) | T12.99 | 0.01 | 99% | 7.3 | 17.0 | 49 / 0 | 0% | 3.51 | none 99%, commander 1%, damage 0% |
| deck_marina_vendrell (gerado; comandante: Marina Vendrell) | T13.00 | 0.00 | 100% | 3.8 | 10.4 | 34 / 0 | 0% | 3.39 | none 100%, damage 0% |
| hope (humano; comandante: Hope Estheim) | T12.96 | 0.08 | 98% | 7.4 | 14.6 | 39 / 0 | 0% | 4.33 | none 98%, alt_win 2% |
| Ivy (humano; comandante: Ivy, Gleeful Spellthief) | T12.93 | 0.12 | 95% | 13.3 | 24.2 | 58 / 0 | 0% | 3.85 | none 95%, damage 5% |
| Karrthus (humano; comandante: Karrthus, Tyrant of Jund) | T11.98 | 1.14 | 42% | 13.0 | 38.4 | 107 / 0 | 0% | 2.46 | damage 58%, none 42% |
| kotis (humano; comandante: Kotis, the Fangkeeper) | T12.76 | 0.28 | 80% | 10.1 | 26.7 | 83 / 0 | 0% | 3.25 | none 80%, damage 20% |
| lightining (humano; comandante: Lightning, Army of One) | T12.97 | 0.03 | 97% | 13.2 | 28.5 | 72 / 0 | 0% | 3.94 | none 97%, damage 3%, commander 1% |
| livaan (humano; comandante: Livaan, Cultist of Tiamat) | T12.93 | 0.08 | 94% | 6.7 | 20.2 | 72 / 0 | 0% | 3.18 | none 94%, damage 6% |
| Rebento de alara (humano; comandante: Child of Alara) | T12.93 | 0.08 | 93% | 7.7 | 24.1 | 74 / 5 | 0% | 2.43 | none 93%, damage 4%, alt_win 3% |
| saheeli (humano; comandante: Saheeli, Sublime Artificer) | T12.75 | 0.38 | 83% | 8.2 | 24.4 | 83 / 0 | 0% | 3.75 | none 83%, damage 17% |
| teval (humano; comandante: Teval, the Balanced Scale) | T12.95 | 0.06 | 96% | 12.8 | 27.1 | 68 / 0 | 1% | 4.43 | none 96%, damage 4% |

- `kotis.txt`: sem script no Forge: Repudiate/Replicate
