# Preços latentes e força de carta (Fase 4)

Gerado por `scripts/fit_prices.py`. Fonte: scripts do Forge compilados em operadores
(Fase 2); nenhuma popularidade. Modelo: `mana_value ≈ b_0 + b_era|produto + π·(E − C)`,
Huber, π ≥ 0 nas features com sinal conhecido, mana como numerário (seção 3.1).

**Veredito:** ACEITE.

Cartas legais compiladas: 31699; no ajuste: 27287; features com preço: 1854 (suporte ≥ 10 cartas). Fora do ajuste: custo alternativo 1153, terreno 1111, várias faces 811, custo X ou sem custo 547, sem data de impressão 399, custo reduzido pela própria carta 233, atom não resolvido 158.

## 1. Mana value fora da amostra — ok

5 dobras. Baseline: média do mana value da mesma linha de tipo no treino.

| faixa de mana value | cartas | erro absoluto médio (π) | baseline |
|---|---:|---:|---:|
| 0–1 | 2764 | 1.32 | 2.21 |
| 2 | 6095 | 0.60 | 1.28 |
| 3 | 6991 | 0.47 | 0.46 |
| 4 | 5429 | 0.80 | 0.59 |
| 5–6 | 5020 | 1.26 | 1.86 |
| 7+ | 988 | 1.85 | 3.93 |
| **todas** | 27287 | **0.845** | 1.229 |

RMSE 1.120 contra 1.533. Erro 31% menor que a baseline.

## 2. Mind's Eye — ok

Força `s_k` = mana value que os efeitos valem − mana value pago, centrada nas cartas do mesmo
custo (prever custo a partir de efeitos regride à média; a centralização tira esse viés) e
absoluta (sem o efeito de era). Intervalo: percentis 5–95 de 30 reamostras do bootstrap
bayesiano (pesos Dirichlet sobre as cartas).
`s_edh`: os mesmos π com os efeitos sob as regras de Commander ("cada oponente" × 3,
gatilhos em ações dos oponentes × 3, vida vale metade com 40).

| carta | mana value | s_k | intervalo 90% | s_edh | |
|---|---:|---:|---|---:|---|
| Sol Ring | 1 | +0.36 | [+0.24, +0.46] | +0.36 |  |
| Lightning Bolt | 1 | +0.67 | [+0.45, +0.84] | +0.67 |  |
| Swords to Plowshares | 1 | +0.24 | [+0.09, +0.47] | +0.21 |  |
| Counterspell | 2 | -0.39 | [-0.50, -0.32] | -0.39 |  |
| Divination | 3 | -0.30 | [-0.48, -0.19] | -0.30 |  |
| Murder | 3 | -0.17 | [-0.31, -0.11] | -0.17 |  |
| Doom Blade | 2 | +0.18 | [+0.06, +0.25] | +0.18 |  |
| Llanowar Elves | 1 | -0.39 | [-0.45, -0.36] | -0.39 |  |
| Rampant Growth | 2 | +0.07 | [-0.07, +0.14] | +0.07 |  |
| Cultivate | 3 | +0.38 | [+0.17, +0.57] | +0.38 |  |
| Grizzly Bears | 2 | -0.08 | [-0.11, -0.08] | -0.08 |  |
| Hill Giant | 4 | -0.17 | [-0.22, -0.17] | -0.17 |  |
| Serra Angel | 5 | +0.60 | [+0.55, +0.63] | +0.60 |  |
| Shivan Dragon | 6 | +0.84 | [+0.77, +0.86] | +0.84 |  |
| Colossal Dreadmaw | 6 | +1.22 | [+1.16, +1.23] | +1.22 |  |
| Phyrexian Arena | 3 | -0.01 | [-0.28, +0.20] | +0.06 |  |
| Necropotence | 3 | +0.11 | [-0.44, +0.93] | +0.11 |  |
| Rhystic Study | 3 | -0.40 | [-0.50, -0.39] | -0.19 |  |
| Smothering Tithe | 4 | -0.93 | [-1.01, -0.88] | -0.83 |  |
| Esper Sentinel | 1 | -0.38 | [-0.47, -0.33] | -0.17 |  |
| Guttersnipe | 3 | +0.01 | [-0.22, +0.18] | +0.89 |  |
| Blood Artist | 2 | -1.00 | [-1.09, -0.81] | -1.09 |  |
| Gray Merchant of Asphodel | 5 | -0.65 | [-0.69, -0.62] | -0.62 |  |
| Mind's Eye | 5 | -1.54 | [-1.62, -1.51] | -1.43 |  |

## 3. Consistência com a dominância (Fase 2) — ok

Pares em que uma carta domina a outra (mesmos efeitos, nada pior): o dominante tem s_k ≥ o
dominado em **1885 de 1893** (99.6%).

- discorda: Cinder Storm (-1.27) ≥ Searing Flesh (+0.42)
- discorda: Void Helix (-0.39) ≥ Stolen Grain (+0.66)
- discorda: Run Aground (-0.66) ≥ Repel (+0.07)
- discorda: Run Aground (-0.66) ≥ Griptide (+0.07)
- discorda: Lost in Space (-0.61) ≥ Uncharted Voyage (+0.12)
- discorda: Demolish (-0.67) ≥ Craterize (-0.11)
- discorda: Violent Impact (-0.39) ≥ Lay Waste (+0.17)
- discorda: Void Helix (-0.39) ≥ Kiss of Death (-0.01)

## Preços π

Preço de cada efeito em mana: soma de π sobre a cadeia de back-off do efeito, medida num
script mínimo do Forge contra a mesma carta sem o efeito (sem tipo nem pips). Coluna
Commander: os mesmos π com as regras do formato ("cada oponente" × 3, vida × ½).
São preços **marginais do design** (1 contra 1): a regressão os comprime em direção à
média, então servem para comparar efeitos entre si, não como custo absoluto de uma carta.

| efeito | design (mana) | Commander (mana) |
|---|---:|---:|
| **Corpo e palavras-chave** | | |
| +1 de poder | +0.37 | +0.37 |
| +1 de resistência | +0.37 | +0.37 |
| Flying (numa 3/3) | +0.44 | +0.44 |
| Trample (numa 3/3) | +0.21 | +0.21 |
| Haste (numa 3/3) | +0.31 | +0.31 |
| Vigilance (numa 3/3) | +0.05 | +0.05 |
| Deathtouch (numa 3/3) | +0.05 | +0.05 |
| Lifelink (numa 3/3) | +0.05 | +0.05 |
| First Strike (numa 3/3) | +0.09 | +0.09 |
| Double Strike (numa 3/3) | +1.07 | +1.07 |
| Menace (numa 3/3) | +0.25 | +0.25 |
| Reach (numa 3/3) | +0.14 | +0.14 |
| Hexproof (numa 3/3) | +0.21 | +0.21 |
| Indestructible (numa 3/3) | +0.05 | +0.05 |
| Ward:2 (numa 3/3) | +0.05 | +0.05 |
| Flash (numa 3/3) | +0.15 | +0.15 |
| Defender (custo, numa 3/3) | -0.48 | -0.48 |
| **Velocidade** | | |
| instantâneo em vez de feitiço | +0.15 | +0.15 |
| **Cartas** | | |
| comprar 1 carta | +0.37 | +0.37 |
| comprar 2 cartas | +0.75 | +0.75 |
| comprar 3 cartas | +1.12 | +1.12 |
| scry 2 | +0.11 | +0.11 |
| surveil 2 | +0.10 | +0.10 |
| tutor: qualquer carta para a mão | +0.48 | +0.48 |
| tutor: criatura para a mão | +0.48 | +0.48 |
| recursão: carta do cemitério para a mão | +0.59 | +0.59 |
| reanimar criatura do seu cemitério | +1.85 | +1.85 |
| comprar 1 carta por turno (manutenção) | +0.65 | +0.65 |
| **Mana** | | |
| {T}: 1 mana incolor | +0.17 | +0.17 |
| {T}: 1 mana de qualquer cor | +0.22 | +0.22 |
| terreno básico da biblioteca para o campo | +0.76 | +0.76 |
| terreno básico da biblioteca para a mão | +0.48 | +0.48 |
| jogar 1 terreno adicional por turno | +0.28 | +0.28 |
| **Tokens** | | |
| criar 1 Treasure | +0.50 | +0.50 |
| criar 1 Clue | +0.17 | +0.17 |
| criar 1 Food | +0.30 | +0.30 |
| criar 1 Blood | +0.17 | +0.17 |
| criar token 1/1 | +0.61 | +0.61 |
| criar dois tokens 1/1 | +1.22 | +1.22 |
| criar token 2/2 | +0.89 | +0.89 |
| criar token 3/3 | +1.18 | +1.18 |
| criar token 4/4 | +1.47 | +1.47 |
| token 1/1 por turno (manutenção) | +0.65 | +0.65 |
| **Remoção e interação** | | |
| destruir criatura | +0.72 | +0.72 |
| destruir artefato | +0.19 | +0.19 |
| destruir encantamento | +0.05 | +0.05 |
| destruir permanente | +1.31 | +1.31 |
| destruir terreno | +1.41 | +1.41 |
| exilar criatura | +0.47 | +0.47 |
| exilar permanente não-terreno | +0.92 | +0.92 |
| devolver criatura à mão | +0.30 | +0.30 |
| −3/−3 até o fim do turno | +0.51 | +0.51 |
| virar criatura | +0.18 | +0.18 |
| anular mágica | +0.15 | +0.15 |
| destruir todas as criaturas | +1.74 | +1.74 |
| cada oponente sacrifica uma criatura | +0.74 | +2.23 |
| oponente alvo descarta 1 | +0.56 | +0.56 |
| cada oponente descarta 1 | +0.42 | +1.26 |
| oponente alvo mói 3 | +0.15 | +0.15 |
| **Dano e vida** | | |
| 1 de dano em qualquer alvo | +0.32 | +0.32 |
| 3 de dano em qualquer alvo | +0.95 | +0.95 |
| 5 de dano em qualquer alvo | +1.58 | +1.58 |
| 3 de dano em criatura | +0.68 | +0.68 |
| 3 de dano em jogador | +0.67 | +0.67 |
| 2 de dano em cada oponente | +0.45 | +1.36 |
| cada oponente perde 2 de vida | +0.42 | +0.64 |
| 2 de dano em cada criatura | +0.85 | +0.85 |
| ganhar 1 de vida | +0.12 | +0.06 |
| ganhar 3 de vida | +0.35 | +0.17 |
| ganhar 5 de vida | +0.58 | +0.29 |
| **Combate** | | |
| +2/+2 até o fim do turno | +0.20 | +0.20 |
| 1 marcador +1/+1 | +0.36 | +0.36 |
| +1/+1 para suas criaturas (estático) | +0.24 | +0.24 |
| suas criaturas ganham Flying (estático) | +0.23 | +0.23 |
| +2/+2 até o fim do turno para suas criaturas | +1.38 | +1.38 |

### Positividade estrita e palavras-chave de combate

Todo efeito e todo custo têm preço ≥ ε = 0.05 por unidade (na chave mais grossa da
cadeia e nas features isoladas): carta + qualquer benefício extra é estritamente mais forte.
As palavras-chave de combate que o relógio simula têm, além disso, um termo por ponto de
poder (`kw|X×power`) com piso derivado do relógio: dano extra até T10 de uma criatura p/p com
a palavra-chave, por ponto de poder, em unidades de +1 de poder, vezes π_poder:

| palavra-chave | dano extra por ponto de poder (em pontos de poder) |
|---|---:|
| double_strike | 0.441 |
| evasive | 0.174 |
| haste | 0.230 |
| trample | 0.138 |

### Palavras-chave: duas estimativas

Sem pisos, o custo impresso **não identifica** palavras-chave de ~0,2 de mana: no modelo
completo sem restrição de sinal o Trample sai −0,1; nas criaturas só com corpo e
palavras-chave, +0,2 a +0,4. Por isso os pisos acima (ε e relógio) — a coluna "modelo
completo" abaixo já é o preço final da parte fixa (sem o termo por poder).

2257 criaturas só com corpo e palavras-chave.

| feature | modelo completo | criaturas simples | cartas |
|---|---:|---:|---:|
| `kw|ETBReplacement` | +0.28 | +1.43 | 63 |
| `kw|Soulshift` | +1.08 | +1.32 | 15 |
| `kw|Cycling` | +0.28 | +0.72 | 36 |
| `kw|etbCounter` | +0.68 | +0.67 | 85 |
| `kw|TypeCycling` | +0.86 | +0.57 | 28 |
| `kw|Bushido` | +0.45 | +0.54 | 15 |
| `kw|Trample` | +0.05 | +0.53 | 145 |
| `kw|Banding` | +0.60 | +0.49 | 19 |
| `body|power` | +0.37 | +0.44 | 2257 |
| `kw|Infect` | +0.45 | +0.41 | 16 |
| `kw|Hexproof` | +0.21 | +0.38 | 25 |
| `body|toughness` | +0.37 | +0.38 | 2257 |
| `kw|Flash` | +0.15 | +0.37 | 71 |
| `kw|Landwalk×power` | +0.07 | +0.35 | 82 |
| `kw|First Strike` | +0.09 | +0.28 | 105 |
| `kw|Flying` | +0.25 | +0.25 | 450 |
| `kw|Double Strike×power` | +0.28 | +0.21 | 28 |
| `kw|Double Strike` | +0.23 | +0.19 | 28 |
| `kw|Menace` | +0.05 | +0.18 | 45 |
| `kw|Exalted` | +0.17 | +0.14 | 18 |
| `kw|Haste×power` | +0.09 | +0.14 | 120 |
| `kw|Flying×power` | +0.07 | +0.12 | 437 |
| `kw|Reach` | +0.14 | +0.05 | 93 |
| `kw|Chapter` | +1.12 | -0.01 | 22 |
| `kw|Menace×power` | +0.07 | -0.02 | 42 |
| `kw|Vigilance` | +0.05 | -0.02 | 128 |
| `kw|Protection` | +0.05 | -0.04 | 94 |
| `kw|Trample×power` | +0.05 | -0.07 | 142 |
| `kw|Lifelink` | +0.05 | -0.13 | 68 |
| `kw|Changeling` | +0.09 | -0.13 | 20 |
| `kw|Ward` | +0.05 | -0.14 | 17 |
| `kw|Prowess` | +0.12 | -0.17 | 32 |
| `kw|Haste` | +0.05 | -0.18 | 123 |
| `kw|Shroud` | +0.05 | -0.19 | 19 |
| `kw|Deathtouch` | +0.05 | -0.19 | 68 |
| `kw|Landwalk` | +0.05 | -0.74 | 83 |
| `kw|Defender` | -0.48 | -0.84 | 61 |
| `kw|Echo` | -0.51 | -1.09 | 17 |
| `kw|Kicker` | +0.05 | -1.50 | 25 |
| `kw|Cumulative upkeep` | -0.24 | -1.54 | 16 |

### Sensibilidade ao piso de velocidade

A curva de design quase não cobra mana por velocidade (Quick Study e Divination custam 3),
mas um instantâneo faz tudo o que o feitiço faz e mais. Até a Fase 8 medir o valor de jogo
da velocidade, `speed|instant` e `kw|Flash` têm um **piso normativo** (padrão 0,15). A
tabela refaz o ajuste e a validação cruzada com cada piso:

| piso | π instantâneo | π Flash | erro fora da amostra | Spearman da força vs piso 0,15 | s(Quick Study) − s(Divination) | s(Murder) − s(Eviscerate) | s(Lightning Bolt) − s(Chain Lightning) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 0.05 | 0.05 | 0.843 | 0.9990 | +0.05 | +0.53 | +0.00 |
| 0.15 | 0.15 | 0.15 | 0.845 | 1.0000 | +0.15 | +0.62 | +0.10 |
| 0.30 | 0.30 | 0.30 | 0.849 | 0.9978 | +0.30 | +0.77 | +0.25 |
| 0.50 | 0.50 | 0.50 | 0.856 | 0.9889 | +0.50 | +0.97 | +0.45 |

Features individuais (a parte específica de cada uma; o resto do preço está nas chaves mais
grossas da cadeia):

| feature | π | intervalo 90% | sinal | cartas |
|---|---:|---|---|---:|
| `body|power` | 0.370 | [0.360, 0.380] | efeito | 14986 |
| `body|toughness` | 0.367 | [0.358, 0.375] | efeito | 15477 |
| `body|big` | 0.000 | [0.000, 0.000] | efeito | 2044 |
| `kw|Flying` | 0.248 | [0.213, 0.278] | efeito | 2735 |
| `kw|Trample` | 0.050 | [0.050, 0.050] | efeito | 803 |
| `kw|Haste` | 0.050 | [0.050, 0.050] | efeito | 550 |
| `kw|Deathtouch` | 0.050 | [0.050, 0.116] | efeito | 273 |
| `kw|Lifelink` | 0.050 | [0.050, 0.050] | efeito | 295 |
| `kw|Flash` | 0.150 | [0.150, 0.150] | efeito | 540 |
| `kw|Defender` | 0.485 | [0.389, 0.567] | custo | 283 |
| `once|Draw|you` | 0.000 | [0.000, 0.000] | efeito | 1063 |
| `turn|Draw|you` | 0.296 | [0.009, 0.586] | efeito | 28 |
| `once|DealDamage|tgt_Any` | 0.000 | [0.000, 0.048] | efeito | 156 |
| `once|Destroy|tgt_Creature` | 0.296 | [0.008, 0.687] | efeito | 415 |
| `once|Destroy|tgt_Permanent` | 0.326 | [0.000, 0.747] | efeito | 39 |
| `once|Counter|tgt_Card` | 0.098 | [0.000, 0.156] | efeito | 210 |
| `once|GainLife|you` | 0.000 | [0.000, 0.000] | efeito | 547 |
| `act|Mana|you` | 0.120 | [0.001, 0.165] | efeito | 537 |
| `once|Token|pt` | 0.143 | [0.120, 0.167] | efeito | 714 |
| `static|pt|yours` | 0.119 | [0.076, 0.169] | efeito | 324 |
| `cost|colored_pips` | 0.587 | [0.549, 0.609] | livre | 24273 |

Maiores preços:

| feature | π | intervalo 90% | sinal | cartas |
|---|---:|---|---|---:|
| `body|variable` | 2.123 | [1.910, 2.236] | efeito | 194 |
| `kw|Modular` | 1.527 | [0.800, 2.060] | efeito | 22 |
| `kw|Sunburst` | 1.520 | [1.166, 2.029] | efeito | 14 |
| `kw|Prototype` | 1.501 | [1.287, 1.789] | efeito | 18 |
| `kw|Graft` | 1.427 | [1.098, 2.106] | efeito | 11 |
| `once|AddPhase` | 1.401 | [0.921, 1.575] | efeito | 11 |
| `once|CopyPermanent` | 1.400 | [0.807, 1.839] | efeito | 66 |
| `kw|Cascade` | 1.271 | [0.893, 1.523] | efeito | 35 |
| `kw|Station` | 1.225 | [0.794, 1.558] | efeito | 26 |
| `once|AddTurn` | 1.225 | [0.413, 2.001] | efeito | 19 |
| `once|DestroyAll|you|?` | 1.219 | [0.517, 1.580] | efeito | 149 |
| `loyalty|Destroy|tgt_Creature|?` | 1.199 | [0.562, 2.117] | efeito | 15 |
| `repl|Draw` | 1.191 | [0.965, 1.517] | livre | 25 |
| `kw|For Mirrodin` | 1.162 | [0.567, 1.758] | efeito | 13 |
| `once|Destroy|tgt_Land` | 1.122 | [0.865, 1.452] | efeito | 74 |
| `type|Planeswalker` | 1.121 | [0.961, 1.277] | livre | 243 |
| `kw|Chapter` | 1.120 | [0.942, 1.274] | efeito | 177 |
| `once|Draw|you|X|?` | 1.116 | [0.839, 1.477] | efeito | 135 |
| `kw|Fabricate` | 1.110 | [0.881, 1.433] | efeito | 16 |
| `repl|GameLoss` | 1.102 | [0.531, 1.391] | livre | 12 |

## Efeito de era e produto

Intercepto por época da primeira impressão (edições do Forge) e por produto (set comum vs
produto de Commander). Negativo = os mesmos efeitos custam menos: power creep.

| grupo | b |
|---|---:|
| 1993–96|set | -0.05 |
| 1997–02|set | -0.07 |
| 2003–08|set | +0.15 |
| 2009–14|commander | +0.30 |
| 2009–14|set | -0.02 |
| 2015–19|commander | +0.20 |
| 2015–19|set | -0.15 |
| 2020–22|commander | +0.09 |
| 2020–22|set | -0.28 |
| 2023+|commander | -0.14 |
| 2023+|set | -0.43 |

## Commander: quem mais sobe

| carta | Δs (Commander − design) |
|---|---:|
| Portal to Phyrexia | +4.45 |
| Vile Mutilator | +2.97 |
| Dusk Mangler | +2.75 |
| Fiery Gambit | +2.71 |
| Skull Rend | +2.59 |
| Lux Artillery | +2.39 |
| Liliana's Triumph | +2.33 |
| Dark Intimations | +2.33 |
| Breath of Malfegor | +2.26 |
| Captured by the Consulate | +1.87 |
| Red Dragon | +1.81 |
| Knight Paladin | +1.81 |

## Extremos (cartas com todos os efeitos precificados)

Mais fortes: Death's Shadow (1, +8.7), Phyrexian Dreadnought (1, +8.1), Apex Devastator (10, +6.0), Etched Monstrosity (5, +5.5), Niv-Mizzet Reborn (5, +5.3), Fiery Gambit (3, +5.2), Moonshadow (1, +5.1), Army of the Damned (8, +5.1), Triplicate Titan (9, +5.0), Worldgorger Dragon (6, +4.8), Boldwyr Heavyweights (4, +4.7), Kozilek, Butcher of Truth (10, +4.7), Ghalta, Stampede Tyrant (8, +4.7), Denizen of the Deep (8, +4.7), Dread Slag (5, +4.7).

Mais fracas: Enter the Infinite (12, -5.9), Primal Surge (10, -4.9), Aladdin's Lamp (10, -4.8), Omniscience (10, -4.8), Soulfire Eruption (9, -4.5), Time Stretch (10, -4.4), Darksteel Forge (9, -4.2), Naya Soulbeast (8, -4.0), Storm Herd (10, -4.0), Gigantomancer (8, -4.0), Soulscour (10, -3.9), Sekki, Seasons' Guide (8, -3.7), Ignition Team (7, -3.6), Inferno Project (7, -3.6), Death by Dragons (6, -3.6).

Leitura: o topo mistura corpos grandes de verdade acima da curva (Gigantosaurus, Progenitus)
com **lacunas do extrator** — drawbacks que a abstração ainda não lê (P/T variável negativo do
Death's Shadow, sacrifício condicional do Phyrexian Dreadnought, marcadores de sono do
Arixmethes). O fundo é dominado por cartas caras de efeito complexo que as features não
capturam por inteiro (turnos extras, cópias, efeitos únicos). O desvio de s_k cresce com o
custo; para cartas de 7+ a força é pouco informativa.

## O que π não mede: tempo

π e s_k são **estáticos**: medem quanto efeito a carta entrega pelo mana, como se o turno
não importasse. No jogo o custo de mana é também **a partir de que turno a carta age**, e
o valor do efeito depende do estado da mesa nesse turno: Rhystic Study é ótima no T2, quando
os oponentes não têm mana sobrando para a taxa, e fraca no T10. Esse valor no tempo,
`v_k(t) = Σ_u γ^u · π · e_k(u | s_u)`, é da Fase 5 (`V(D)` com o relógio); aqui π é o
preço por unidade de efeito que ela usa.

## Limites

- π herda o julgamento de design da WotC (seção 3.1): é um prior, não a verdade; as âncoras
  terminal (relógio) e de oráculo entram nas Fases 5 e 8.
- Linear e aditivo: interações dentro da carta (voar num 5/5 vale mais que num 1/1) só
  aparecem pelo termo `body|big`.
- Ativadas são descontadas por `1/(1 + custo de ativação)`, uma escolha de modelagem.
- Cartas com custo X, custo alternativo, várias faces ou terrenos ficam fora do ajuste.
- Tempo: 312s com 30 reamostras.
