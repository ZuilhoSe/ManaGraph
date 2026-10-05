# Relógio de dano (Fase 2.5)

Gerado por `scripts/clock_report.py`. Cartas reais compiladas dos scripts do Forge;
300 ordens de compra por deck, horizonte T12, oponentes como estoque de
bloqueadores (0.75/turno a partir do T2; bloqueio chump gasta o bloqueador).
Vitória: 120 de dano, 30 de veneno, 63 de dano de comandante ou biblioteca vazia
(3 oponentes). Goldfish abstrato: os oponentes não jogam, só bloqueiam.

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

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---|
| stomp verde | T12.62 | 0.69 | 79% | 10.3 | 28.0 | 3.75 | none 79%, damage 21% |
| 10 mais fortes → 10 draw | T12.88 | 0.19 | 92% | 7.0 | 22.3 | 4.29 | none 92%, damage 8% |

Deck: 37 Forests, 12 ramp (Llanowar Elves, Elvish Mystic, Fyndhorn Elves, Rampant Growth, Cultivate, Kodama's Reach, Wood Elves, Farhaven Elf, Nature's Lore, Three Visits, Sol Ring, Arcane Signet), 30 criaturas mono-verdes sem habilidades que o relógio lê (6 por mana value 2–6, ordem alfabética do catálogo), finalizadores (Overrun, Craterhoof Behemoth, Overwhelming Stampede), 15 mágicas verdes sem efeito no relógio. Trocadas: Aboroth, Aggressive Mammoth, Adaptive Snapjaw, Alpha Tyrranax, Ambush Krotiq, Ancient Greenwarden, Afterburner Expert, Ainok Artillerist, Agatha's Champion, Ageless Entity. Draw: Harmonize, Divination, Concentrate, Tidings, Inspiration, Opportunity, Sign in Blood, Night's Whisper, Phyrexian Arena, Read the Bones.

### Achado: a primeira versão deste teste

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---|
| lista de ameaças à mão | T11.69 | 1.56 | 34% | 20.1 | 49.7 | 3.21 | damage 66%, none 34% |
| 10 primeiras → 10 draw | T11.67 | 1.85 | 37% | 18.4 | 51.0 | 3.29 | damage 63%, none 37% |

Trocadas: Colossal Dreadmaw (6), Pelakka Wurm (7), Craw Wurm (6), Krosan Colossus (9), Thragtusk (5), Garruk's Packleader (5), Primeval Titan (6), Terastodon (8), Woodfall Primus (8), Ancient Brontodon (8). Várias custam 8–9 manas e quase não entram até T12; o modelo diz, corretamente, que compra barata vale mais que elas nesse horizonte. Por isso o critério 3 acima troca as mais fortes **lançáveis**.

## Sensibilidade aos parâmetros do oponente

`P_conectar` e a capacidade de bloqueio são parâmetros até a Fase 8 (risco do plano).
A conclusão do critério 3 precisa valer na faixa toda:

| taxa de bloqueadores/turno | P_conectar (evasão) | dano T8 stomp | dano T8 trocado | stomp melhor? |
|---:|---:|---:|---:|:---:|
| 0.25 | 0.7 | 41.9 | 32.6 | sim |
| 0.25 | 0.85 | 42.1 | 32.8 | sim |
| 0.25 | 1.0 | 42.2 | 32.7 | sim |
| 0.75 | 0.7 | 27.1 | 21.9 | sim |
| 0.75 | 0.85 | 27.3 | 22.1 | sim |
| 0.75 | 1.0 | 27.4 | 22.0 | sim |
| 1.25 | 0.7 | 18.5 | 15.2 | sim |
| 1.25 | 0.85 | 18.7 | 15.4 | sim |
| 1.25 | 1.0 | 18.9 | 15.4 | sim |

## Decks do repositório

Decks gerados pelo sistema antigo (`data/`) e decks humanos (`lucas_plans/Decks/`).
O relógio mede só a rota de dano/veneno/comandante/mill em goldfish: um deck de
controle ou combo condicional aparece lento aqui por construção.

| deck | relógio médio | variância | sem vitória até T | dano T6 | dano T8 | mana desperdiçada/turno | terminais |
|---|---:|---:|---:|---:|---:|---:|---|
| deck_ertai_resurrected (gerado; comandante: Ertai Resurrected) | T12.94 | 0.07 | 94% | 9.7 | 25.0 | 3.11 | none 94%, damage 6% |
| deck_krenko_mob_boss (gerado; comandante: Krenko, Mob Boss) | T9.97 | 1.56 | 8% | 6.1 | 48.2 | 2.99 | damage 92%, none 8% |
| deck_lightning_army_of_one (gerado; comandante: Lightning, Army of One) | T12.99 | 0.01 | 99% | 7.4 | 17.0 | 3.52 | none 99%, commander 1%, damage 0% |
| deck_marina_vendrell (gerado; comandante: Marina Vendrell) | T13.00 | 0.00 | 100% | 4.1 | 10.9 | 3.81 | none 100%, damage 0% |
| hope (humano; comandante: Hope Estheim) | T13.00 | 0.00 | 100% | 7.6 | 14.9 | 4.61 | none 100% |
| Ivy (humano; comandante: Ivy, Gleeful Spellthief) | T12.99 | 0.01 | 99% | 13.1 | 23.3 | 3.80 | none 99%, damage 1% |
| Karrthus (humano; comandante: Karrthus, Tyrant of Jund) | T12.11 | 1.17 | 51% | 13.0 | 37.5 | 3.07 | none 51%, damage 49% |
| kotis (humano; comandante: Kotis, the Fangkeeper) | T12.84 | 0.20 | 86% | 10.0 | 25.9 | 3.33 | none 86%, damage 14% |
| lightining (humano; comandante: Lightning, Army of One) | T12.97 | 0.03 | 97% | 13.2 | 28.4 | 3.97 | none 97%, damage 3%, commander 0% |
| livaan (humano; comandante: Livaan, Cultist of Tiamat) | T12.99 | 0.02 | 99% | 6.6 | 19.4 | 3.56 | none 99%, damage 1% |
| Rebento de alara (humano; comandante: Child of Alara) | T12.99 | 0.01 | 99% | 6.1 | 21.5 | 2.73 | none 99%, damage 1% |
| saheeli (humano; comandante: Saheeli, Sublime Artificer) | T12.92 | 0.11 | 94% | 7.1 | 19.5 | 4.16 | none 94%, damage 6% |
| teval (humano; comandante: Teval, the Balanced Scale) | T12.97 | 0.04 | 97% | 12.8 | 26.8 | 4.55 | none 97%, damage 3% |

- `kotis.txt`: sem script no Forge: Repudiate/Replicate
