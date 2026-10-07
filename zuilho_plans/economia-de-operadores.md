# Economia de operadores — sinergia deduzida das regras

> Status: em execução — Fases 0, 1, 2, 2.5 (com 2.5b: todas as rotas de vitória) e 3 aceitas;
> relógio de dano, taxonomia de arquétipos e exploração adicionados (2026-10-05). Substitui a ideia de "sinergia =
> similaridade textual" e o score linear com pesos escolhidos à mão. Convive com
> [`ONTOLOGY.md`](../ONTOLOGY.md): a ontologia vira a interface tipada desta
> álgebra; o Forge vira a fonte e o oráculo das regras.

## 0. Por que mudar

### O que está quebrado hoje

Evidência nos próprios artefatos do repositório:

- `data/solved_deck_log.json` (Krenko): Scrying Glass, Aladdin's Lamp,
  Jodah's Codex, Mind's Eye, Jandor's Ring, Mana Cylix, Goblin Masons…
- `data/deck_lightning_army_of_one.json`: ~32 terrenos, quase todos
  não-básicos inúteis para Boros (Sliver Hive, Heap Gate, Rainbow Vale,
  Hall of Tagsin, Lotus Field, Forbidden Orchard), com **1 Mountain e
  1 Plains**. O Supervisor aprovou.

Causas mecânicas, em ordem de gravidade:

1. **Não há termo de força.** Nenhum dos ~14 termos de
   `DeckSolver._score_parts` mede efeito por custo. Mind's Eye e Rhystic Study
   são equivalentes para o score: mesmo papel, texto parecido.
2. **"Sinergia" é cosseno de texto** entre comandante e carta. Mede semelhança
   de redação, não complementaridade funcional. Um gerador de Treasure e um
   payoff de "artifact enters" não compartilham palavras.
3. **Terrenos:** abaixo da cota, todo terreno ganha `land_urgent = +3.0`; os
   básicos só entram como fallback em `fill()`, e o desempate entre
   não-básicos é o cosseno com o comandante mais `mana_bonus` por "produz
   cores". Resultado: Rainbow Vale vence Mountain.
4. **Pesos à mão sem calibração** (2.0, 1.4, 3.0, 2.2, 1.6, 0.45…), cada um
   remendando uma falha observada.
5. **`_ontology_pair_score` diverge da especificação.** O `ONTOLOGY.md`
   (Layer 3) especifica `Σ w · min(supply, demand)`, saturante. O código faz
   `len(card.emits & deck.rewards)` sobre a união do deck: sem contagem, sem
   saturação, e premiando cartas prolixas.
6. **Não há métrica de resultado.** O gate aprova decks legais, e as métricas
   da ontologia medem coerência segundo o próprio modelo.
7. **Seleção gulosa:** uma carta por vez, sem visão do deck final.

### Restrição de projeto (inegociável)

**Nada de EDHREC/Moxfield como sinal de treino ou de score.** O objetivo é
encontrar decks bons *e não populares*. Popularidade só pode entrar **a
posteriori**, como instrumento de medida de novidade.

**Também não queremos decks jogando partidas completas** como função de
avaliação.

## 1. Tese

Sem partidas e sem popularidade não há sinal de resultado para *aprender*
sinergia como o AlphaZero aprendeu Go. O AlphaZero tinha um sinal terminal
(vitória ou derrota) e um simulador perfeito.

O que temos são **as regras e os textos das cartas, que já definem as
interações de forma completa**. Sinergia é consequência lógica: ela é
**deduzida**, não descoberta estatisticamente. O aprendizado fica restrito a
duas coisas:

- a **tradução** de texto/script em operadores (supervisionada pelo Forge);
- os **preços latentes** dos recursos (identificados pelo próprio catálogo).

Os embeddings falharam porque tentavam aprender do texto algo que já está
escrito nele em forma lógica.

## 2. Modelo formal

### 2.1 Estado abstrato

O mapa exato de estados é impossível. Magic é Turing-completo (Churchill,
Biderman & Herrick, 2019), então decidir o resultado de um estado arbitrário é
indecidível. Trabalhamos com uma **abstração**:

```
s = ( r ∈ ℝ^R ,  f ∈ {0,1}^F )
r: recursos  — mana{W,U,B,R,G,C}, cartas na mão, corpos, tokens, contadores,
               vida, dano causado, veneno, artefatos/encantamentos em jogo,
               cartas no cemitério, ...
f: flags     — can_lose, can_win, hexproof(commander), indestructible(...), ...
```

Os estados terminais são definidos pelas regras: vida ≤ 0, 10 de veneno, 21 de
dano de comandante, vitórias alternativas. Os terminais podem ser **mascarados**
por flags (`can_lose = 0`).

### 2.2 Operadores

Cada carta é um conjunto de operadores.

- **1ª ordem — transformam recursos.**
  - Ativado/estático: `custo c ∈ ℝ^R → efeito e ∈ ℝ^R`.
  - Disparado: `evento E → efeito e` (com condição e frequência).
  - Exemplo: Sol Ring, `{1} → mana{C}=2`, repetível uma vez por turno.
- **2ª ordem — transformam outros operadores ou as regras de término.**
  - Multiplicadores de aresta: Doubling Season, dobradores de gatilho.
  - Redutores de custo: alteram `c` de uma classe de operadores.
  - Conversores: lifelink transforma `dano → dano + vida`.
  - Máscaras: "can't lose", "can't win", "can't cast", hexproof.

Os operadores de 2ª ordem são o que torna doublers e redutores sinérgicos com
*todo o resto*: eles escalam pesos de arestas, não somam um bônus fixo.

### 2.3 Grafo de cartas

| Camada | Objeto matemático |
|---|---|
| Cartas | nós com vetor custo→efeito |
| Interações de 1ª ordem | arestas tipadas (`emits→rewards`, `produces→consumes`, `tutors→member`), peso = taxa |
| Interações de 2ª ordem | modificadores de aresta (escalares, máscaras) |
| Valor | preços latentes π ∈ ℝ^R sobre os recursos |
| Engines/combos | raio espectral ρ(G) do subgrafo induzido |
| Interação | ΔV negado num grafo adversário |
| Deck | x ∈ {0,1}^n |

### 2.4 Valor de um deck

```
V(D) = Σ_{t=1..T} γ^t · π · E[ r_t | D ]
```

- `T` é o horizonte (~8–10 turnos em Commander).
- `E[r_t | D]` usa a probabilidade de cada carta estar disponível no turno `t`
  (hipergeométrica; já existe em `src/hypergeometric.py`) e a mana disponível.
- **Sinergia de um conjunto S:** `syn(S) = V(S) − Σ_{s∈S} V({s})`.
  - `syn > 0`: sinergia. `syn < 0`: **anti-sinergia** (Rest in Peace +
    recursão, efeitos simétricos), detectada pelo mesmo cálculo.

Exemplo derivado, não aprendido: com `a` artefatos e `k` cartas
"artifact ETB → draw 1",

```
E[cartas extras] ≈ Σ_t P(≥1 payoff em jogo em t) · E[artefatos que entram em t]
```

Isso é aproximadamente **bilinear em a·k**: o valor marginal de cada artefato
cresce com `k`, e o de cada payoff cresce com `a`. Essa é a regra "muitos
artefatos é bom". A mana por turno limita `E[artefatos que entram em t]`,
então o termo **satura**, e o modelo sabe quando parar.

### 2.5 Loops e combos: raio espectral

Restrito aos operadores **repetíveis** do deck (sem "once per turn", ou com
untap), defina a matriz de ganho

```
G_ij = unidades do recurso j obtidas ao investir 1 unidade do recurso i
```

- `ρ(G) ≥ 1`: o loop se sustenta. Engine ou combo infinito.
- `ρ(G)` próximo de 1: engine forte.
- `ρ(G) ≪ 1`: conversão com perda.

É a estrutura do modelo insumo-produto de **Leontief** (condição de
Hawkins–Simon para economia produtiva). Isso dá um critério, sem popularidade,
para hiperarestas de 3 ou mais cartas, que nem a sinergia par a par nem os
embeddings capturam. **Hiperarestas com ρ alto entre cartas de texto
dissimilar são a hipótese de descoberta.**

### 2.6 Arquétipos de primeira ordem: o relógio de dano

**O problema.** Um mono-green stomp (ramp, criaturas grandes, trample,
overrun) quase não tem arestas `emits → rewards` ou `produces → consumes`
entre nomes de predicado. Se o objetivo for só `syn(S)`, o otimizador
**sempre prefere decks de engine**. É um viés estrutural, não um bug pontual.

**A correção:** o objetivo é `V(D)`, não `syn(S)`, e `V` precisa de um
**relógio de dano** explícito. O valor de um stomp é quase todo de primeira
ordem (stats por mana) mais **tempo**:

```
m_t        = terrenos_t + Σ ramp disponível em t            # mana no turno t
L_t(D)     = conjunto lançado até t, por uma política de curva (maior impacto que cabe em m_t)
dano_t     = Σ_{c ∈ corpos em jogo, sem enjoo} poder_c · P_conectar(c) · mult_t
relógio(D) = min{ t : Σ_{τ ≤ t} dano_τ ≥ 120 }               # 3 oponentes × 40
```

As peças viram operadores que já existem na álgebra:

1. **Mana no tempo.** Ramp é `produces(mana)`; cada carta consome seu cmc.
   A sinergia ramp × topo de curva é **temporal**: ramp vale mais quanto mais
   caras forem as ameaças. Exemplo: um 7/7 de 7 manas sai no turno 7 sem ramp
   e no turno 5 com duas rampas — dois ataques a mais, ≈ 14 de dano. É um
   termo quadrático real em `W` entre fontes de mana e ameaças caras, com peso
   `turnos antecipados × poder`, sem precisar de palavra em comum.
2. **Corpo = conversor de mana em dano por turno:** `poder × P_conectar`.
3. **Trample e evasão** são operadores de 2ª ordem sobre `P_conectar`.
   **Overrun** (Craterhoof, Overwhelming Stampede) multiplica o poder de todos
   os corpos — o valor dele cresce com o número de criaturas, e "ter muitas
   criaturas é bom" sai como conclusão, com a mesma estrutura bilinear do
   exemplo dos artefatos (seção 2.4).
4. **Proteção em massa** (Heroic Intervention, hexproof) reduz a perda
   esperada contra o modelo de oponente.

**Combate contra bloqueadores** é a parte difícil: `P_conectar` depende do
oponente. Abstração viável: cada oponente tem uma **capacidade de bloqueio
por turno** `b_t` (quantos corpos absorve). Trample e evasão contornam `b_t`;
overrun e excesso de corpos saturam `b_t`. Até a Fase 8, `b_t` vem da taxa-base
do catálogo (seção 4, opção 1); depois, da população coevoluída — e aí stomp
vale mais justamente quando a população tem poucas remoções em massa.

O relógio também dá o **numerário**: dano. Outros recursos (cartas, vida,
tokens) convertem para dano pelos preços `π` da seção 3, e o mesmo relógio
mede decks de engine, que precisam converter o motor em dano ou em outra
condição de vitória. Veneno, dano de comandante, biblioteca vazia e vitórias
alternativas são **relógios paralelos** (seção 2.7, eixo A); `V` usa o mais
rápido.

### 2.7 Taxonomia de arquétipos: três eixos

"Stomp" e "aristocrats" dizem **como** o deck ganha; "midrange" e "controle"
dizem **em que ritmo e com quanta interação** ele joga. São eixos diferentes.
Um arquétipo é uma combinação de três eixos:

**Eixo A — rota até a vitória (como ganha).** Toda rota é um caminho no
grafo de recursos, da mana até um terminal das regras (CR 104):

| Terminal | Rotas |
|---|---|
| Dano de combate | go-wide (tokens, tribal com lordes), go-tall (stomp), evasão (voadores, unblockable), combates extras |
| Dano de comandante (21) | voltron (equipamentos, auras) |
| Perda de vida fora de combate | aristocrats (morte → drenar), burn/spellslinger, lifegain → drenar, pings de ETB, group slug (punir oponentes por agir) |
| Veneno (10) | infect, toxic, proliferate |
| Biblioteca vazia | mill dos oponentes |
| Vitória alternativa | Thassa's Oracle / Laboratory Maniac (com self-mill), Approach of the Second Sun, Coalition Victory, … |
| Loop infinito | combo: ciclo com ganho ≥ 1 (seção 2.5) alimentando qualquer terminal acima |

**Eixo B — postura (ritmo e relação com os oponentes):**

| Postura | Assinatura mecânica |
|---|---|
| Aggro | relógio rápido, pouca interação, curva baixa |
| Midrange | relógio médio, interação média, **valor por carta alto** (2-por-1, ameaças resilientes); se adapta à mesa |
| Controle | relógio lento, muita interação, vantagem de cartas, vence tarde |
| Tempo | ameaças e interação baratas; nega mana e tempo dos oponentes |
| Combo | velocidade de montagem, tutores, proteção; pouca interação para fora |
| Ramp / big mana | acelera até efeitos desproporcionais |
| Stax / prisão | restrições (MASK) sobre os recursos dos oponentes |
| Pillowfort | desestimula ataques (Propaganda, Ghostly Prison) |
| Group hug / política | dá recursos aos oponentes e ganha pela dinâmica da mesa |

**Eixo C — motor de recursos (o que alimenta o deck):** cemitério
(reanimator, recursão, self-mill), terrenos/landfall, artefatos,
encantamentos (enchantress), tokens, contadores +1/+1, mágicas (storm,
magecraft), blink/ETB, sacrifício, ganho de vida, compra e wheels, tribal,
roubo, cópia/clone.

**Exemplos:** reanimator controle = (combate ou vitória alternativa,
controle, cemitério); stomp = (combate go-tall, ramp, terrenos); aristocrats
midrange = (drenar, midrange, sacrifício).

**Posturas viram descritores contínuos**, medidos só pelas regras — sem
rótulos:

| Descritor | Medida |
|---|---|
| Velocidade | turno do relógio (seção 2.6), o mais rápido entre os terminais |
| Interação | densidade de respostas, ponderada por velocidade (instantâneo > feitiço) |
| Valor | cartas líquidas por turno + corpos que sobrevivem a remoção |
| Restrição | densidade de operadores MASK que afetam oponentes (stax) |
| Doação | recursos entregues a oponentes (group hug) |
| Ordem | fração de `V` vinda de 1ª vs 2ª ordem (stomp vs engine) |

Midrange é a região de velocidade média, interação média e valor alto;
controle é velocidade baixa, interação alta e valor alto. Os nomes são só
regiões de um espaço contínuo, e é esse espaço que a exploração da seção 6.1
usa como descritores. O produto dos três eixos tem centenas de células; a
comunidade ocupa uma fração delas.

**O que o modelo ainda não vê bem:**
- **Group hug / política:** o valor depende do comportamento dos outros
  jogadores (alianças, ameaça percebida). A classe `adversarial` da álgebra
  usa minimax; isso é pessimista para política e fica fora até existir um
  modelo de comportamento de oponente.
- **Roubo e cópia:** têm semântica na álgebra v1.1 (`GainControl` como
  recurso, cópia como `reference`), mas o valor é diferido para a Fase 5
  (`data/ontology/ALGEBRA_COVERAGE.md`). Até lá, motores de "usar as cartas
  dos oponentes" ficam subavaliados.
- **Pillowfort:** modelável como imposto sobre os ataques dos oponentes, mas
  o valor depende do modelo de ameaça da Fase 8.

## 3. Valor como estado latente

As taxas de troca (quanto vale uma carta, um token, 1 de vida, em mana) são
**variáveis latentes**. Para identificá-las, precisamos de observáveis. Há três,
nenhum envolvendo popularidade:

### 3.1 Preços sem arbitragem (âncora principal)

Cada carta `k` é uma troca: o custo `C_k` compra o efeito `E_k`. Se o design é
aproximadamente balanceado, existem preços `π` tais que `π·C_k ≈ π·E_k`.

```
π* = argmin_π  Σ_k  Huber( π·E_k − π·C_k − b_era(k) )   +  λ‖π‖²
     s.a.  π ≥ 0,  π_mana = 1   (numerário)
```

- `b_era` absorve o power creep como efeito de nuisance.
- **Força da carta:** `s_k = π*·E_k − π*·C_k`, o resíduo. Carta acima da
  curva tem `s_k > 0`.
- **Ressalva honesta:** isso codifica o julgamento de design da R&D da WotC. É
  um prior, não verdade absoluta, mas não é popularidade.

### 3.2 Ancoragem terminal

No limite, valor é `ΔP(atingir estado terminal)`. Propagar isso exige
transições (TD-learning) num **MDP abstrato** sobre `s`, que é barato e não
envolve partidas completas. É a âncora secundária, usada para checar `π`.

### 3.3 Consistência com o oráculo

Os `π` inferidos precisam prever os Δ medidos nos micro-experimentos do Forge
(seção 5).

### 3.4 Como medir uma latente

- **Fora da amostra:** prever o cmc de cartas fora do ajuste.
- **Estabilidade:** o deck ótimo muda pouco quando `π` varia dentro do
  intervalo de confiança.
- **Concordância** entre as três âncoras.

## 4. Interação: valor de negação

Remoção vale porque **impede o oponente de fazer algo**. Formalmente:

```
valor(resposta a) = E_o [ V_opp(o) − V_opp(o | a remove/neutraliza um nó ou aresta) ]
```

Precisamos de uma distribuição de oponentes `o`. Opções sem popularidade:

1. **Taxa-base do catálogo.** P(ameaça de classe c) pela distribuição de
   cartas legais. Simples e ingênua.
2. **Robustez (minimax).** O deck precisa sobreviver ao pior tipo de engine
   adversário. Zero respostas a encantamento vira perda ilimitada contra
   engines de encantamento. Isso formaliza "não ter interação é ruim".
3. **Meta endógeno (preferida).** Os oponentes são **os decks gerados pelo
   próprio otimizador**: uma população coevoluída, no estilo AlphaStar League.
   O meta emerge das regras, não do público. A avaliação é operador contra
   operador (a resposta remove um nó do grafo adversário e mede ΔV), não
   partida completa.

Até a fase 8, interação entra como **restrição**: ≥ N respostas por classe
(criatura, artefato, encantamento, pilha, cemitério, board).

## 5. Forge: fonte e oráculo das regras

Não reimplementar as regras. O Forge tem dois papéis:

1. **Fonte de scripts** (uso atual): `T:Mode$ ChangesZone | Destination$
   Battlefield | ValidCard$ Artifact.YouCtrl | Execute$ TrigDraw` já é um
   operador formal.
2. **Função de transição.** O **Puzzle Mode** define um estado inicial em
   arquivo texto (vida, mão, permanentes, anexos, biblioteca, turnos). Isso
   permite **micro-experimentos controlados**: colocar A e B em jogo, executar
   uma ação e medir o Δ do vetor de recursos. Não há documentação de uso
   programático dos puzzles; provavelmente será preciso chamar a API Java
   (JPype ou um wrapper mínimo).

### 5.1 Cobertura em três níveis

| Nível | Pergunta | Hoje |
|---|---|---|
| Scriptada | a carta existe no Forge? | 33.760 / 38.651 (os não casados são quase todos token/plane/emblem) |
| Abstraível | o script cabe na álgebra de operadores? | **desconhecido — Fase 1** |
| Fiel | o operador abstrato prevê o Δ medido no Forge? | **desconhecido — Fase 3** |

## 6. Otimização sem greedy

Com `V` aproximada até 2ª ordem:

```
max_x   cᵀx + xᵀWx + Σ_R w_R · z_R
s.a.    Σ x = 99 − (terrenos fixos);  identidade;  singleton;  legalidade
        z_R ≤ supply_R(x),  z_R ≤ demand_R(x)          # saturação linearizada
        y_ij ≤ x_i,  y_ij ≤ x_j                         # bilinear linearizado
        Σ p_i x_i ≤ B;  p_i ≤ P_max                      # orçamento
        respostas_c(x) ≥ N_c  ∀ classe c                 # interação (até F8)
        x ∈ {0,1}^n
```

- É uma **mochila quadrática**: MIQP/MILP com centenas de candidatos, tratável
  por OR-Tools (CP-SAT) ou SCIP.
- O ILP dá o deck inicial; uma **busca local** (trocas 1↔1 e pacotes 2↔2,
  simulated annealing ou tabu) trata os termos não lineares: `ρ(G)` e
  operadores de 2ª ordem.
- Não é preciso *aprender o otimizador*. O aprendizado vai para `V` (operadores
  e `π`).

### 6.1 Exploração por diversidade (quality-diversity)

Otimizar `V` converge para *um* deck. Para ver o espaço inteiro — inclusive
o que ninguém testou — a busca é por diversidade, sem popularidade:

- **Rotas primeiro.** Enumerar as rotas mana → terminal (eixo A da seção 2.7)
  no grafo de operadores do catálogo. Para cada rota: **suporte** (nº de
  cartas legais na aresta gargalo), **relógio** (seção 2.6) e
  **fragilidade** (quantas classes de resposta da seção 4 quebram a rota).
  Rotas com ciclos são engines; ciclos com ganho `≥ 1` são os loops da
  seção 2.5.
- **MAP-Elites** (Mouret & Clune, 2015; Fontaine et al., 2019 para decks de
  Hearthstone): o arquivo guarda o melhor deck de cada célula. Células =
  rota dominante (eixo A) × motor (eixo C) × descritores de postura
  contínuos (seção 2.7).
- **Novelty search** (Lehman & Stanley, 2011): premia o deck por ser
  comportamentalmente distante do arquivo, não por ser bom. Novidade medida
  **internamente** (distância entre vetores de descritores).
- **Combinação:** a novidade empurra a busca para regiões novas; o MAP-Elites
  guarda o melhor de cada região.

**Definição operacional de descoberta:** uma célula (rota × postura × motor)
com elite forte (`V` alto, relógio competitivo) que nenhuma heurística, LLM
ou base de popularidade teria visitado. Verificável a posteriori comparando
o arquivo com a inclusão observada (Fase 9).

### 6.2 Modelo fundacional

Três camadas, todas treinadas sem popularidade:

| Camada | O que aprende | Dados |
|---|---|---|
| **Codificador de cartas** | representação *funcional* de cada carta (não textual) | scripts do Forge + oracle; tarefas autossupervisionadas: texto → operador (o compilador da Fase 2 dá os rótulos), operador mascarado, previsão de custo (a tarefa de `π`) |
| **Modelo de mundo** | transição `s' = f(s, ação)` no estado abstrato (2.1) | micro-experimentos do Forge (Fase 3): estados legais aleatórios + ações → Δ. É aprender a física do jogo, não deck jogando partida |
| **Valor e política de decks** | `V(D)` amortizado (Deep Sets / Set Transformer) + gerador de decks | o **próprio arquivo** do MAP-Elites |

- **Ciclo de autoaperfeiçoamento** (o análogo possível do AlphaZero): busca →
  arquivo → treino do valor e do gerador → busca melhor. O AlphaZero treinava
  com as próprias partidas; aqui o modelo treina com as **próprias buscas**,
  ancorado nas regras, nunca em dados humanos.
- **Argumento principal para o codificador:** Magic lança cartas o tempo
  todo. Um modelo que lê scripts/texto funcional avalia uma coleção nova
  **sem retreinar**; embeddings de texto e popularidade não fazem isso.
- **Ordem:** o modelo fundacional não é o ponto de partida; ele emerge das
  fases anteriores. O compilador da Fase 2 é o tokenizador; `π` (Fase 4) é a
  primeira tarefa de pré-treino; rotas + MAP-Elites geram os dados; o
  surrogate de `V` é a rede de valor.

### 6.3 Limites da exploração

- **Goodhart:** um ciclo que se autoaperfeiçoa explora as falhas de `V`. Se
  `V` superestima uma interação, o gerador enche os decks dela. Âncora:
  validação no Forge (micro-experimentos e poucas partidas de checagem),
  **fora** do laço.
- **O modelo não sabe mais do que a abstração:** classes de valor diferido
  (`reference`, `layer`, `adversarial`, …) só pesam quando a fase que as
  resolve existir; rotas que dependem delas ficam subexploradas até lá.
- **Escala:** o codificador é viável com os dados existentes; o modelo de
  mundo exige milhões de transições e é o ponto caro do projeto.

## 7. Passo a passo

Cada fase tem um entregável, um critério de aceite e um critério de parada
(quando a hipótese falhou e é preciso repensar).

### Fase 0 — Consertar o que já está errado (≈ 2–4 dias)

Independe do resto e reduz o ruído das comparações futuras.

- [x] Terrenos: básicos na proporção dos pips (Karsten, `hypergeometric.py`)
      como default. Não-básicos entram **só por troca** com
      `Δ = ΔP(fontes corretas em t) − custo(entra virado) − custo(drawback) + utilidade > 0`.
      → `src/deck_analysis/land_value.py`; `DeckSolver._upgrade_lands`.
      ΔP aproximado: peso 1 para cor abaixo do piso de fontes, 0 acima; fontes
      contadas só em terrenos (o `diagnose` conta pedras/Treasure como `any`).
- [x] Remover `land_urgent` (removido para todos os terrenos: terrenos não
      disputam slots com mágicas; `_best_land` preenche a cota) e o cosseno com
      o comandante para terrenos (`synergy = 0`).
- [x] Drawbacks de terreno como predicados (dá mana a oponente, sacrifica
      terrenos, cor condicional a tipo, custo extra, face da frente não-terreno).
- [x] `_ontology_pair_score` alinhado ao `ONTOLOGY.md`: contagens com `min`
      saturante (`Σ min(supply, capacidade · demand)`, valor marginal da carta).

**Aceite:** o Lightning reconstruído tem ≥ 20 básicos e nenhum terreno que
gere mana para oponentes ou seja de outra tribo.
→ Base de mana do `data/deck_lightning_army_of_one.json` refeita: 4 → 32
básicos, 35 terrenos, só Plateau / Sunbaked Canyon / Sunbillow Verge como
não-básicos. Build completa do zero (2026-10-04): 37 terrenos, **23 básicos**,
14 não-básicos todos R/W ou busca de básico, nenhum nocivo ou tribal. A lentidão
da busca híbrida (> 30 min) era um JOIN sem índice em `ontology.search`,
corrigido na limpeza de código (consulta de > 400 s para ~3 s).

### Fase 1 — Auditoria de cobertura do Forge (≈ 1 semana)

- [x] Inventariar os tipos de habilidade nos scripts (`A:`, `T:`, `S:`, `R:`,
      `SVar:`), com seus `Mode$`, `ApiType`, `ValidCard$`, `Execute$`.
      → `scripts/forge_ability_inventory.py` (átomos `api:`/`trigger:`/`static:`/
      `replacement:`/`keyword:`/`cost:`; `ChangeZone` separado por zonas,
      `Continuous` separado pelo efeito).
- [x] Definir a **álgebra mínima**: recursos R, eventos, flags F, operadores de
      1ª e 2ª ordem. → `data/ontology/operator_algebra_v1.yaml`.
- [x] Mapear cada tipo de habilidade para a álgebra, ou marcar como fora dela
      (775 de 794 átomos mapeados; o resto conta como fora).
- [x] Relatório: % de cartas totalmente abstraíveis, famílias fora (layers,
      controle, cópia, alternate win, escolhas), top-N átomos que mais quebram.
      → `scripts/forge_algebra_coverage.py` → `data/ontology/ALGEBRA_COVERAGE.md`.

**Aceite:** ≥ 80% das cartas legais em Commander totalmente abstraíveis.
**Parada:** < 60% → repensar a granularidade da álgebra antes de seguir.

**Resultado (2026-10-04): ACEITE.** 31.699 cartas legais com script (131 do
catálogo sem script, quase todas stickers).

- Álgebra v1.0: strict 91,8%, com aproximação 93,1%. Famílias fora: cópia
  (791), layers (715), informação oculta (263), turno (189), vitória
  alternativa (85), escolha do oponente (79).
- Álgebra v1.1 (as famílias ganharam semântica): **strict 98,0%, com
  aproximação 100%**, cota pessimista 91,1%. Classes novas:
  - `reference` — cópia: o efeito é o operador de outro nó (CR 707).
  - `layer` — reclassificar / fixar P/T / remover operadores, na ordem do
    CR 613 (`layer_index` no YAML).
  - `face_down` — corpo 2/2 sem operadores + custo de virar (CR 708).
  - `turn` — turno/fase/combate extra multiplica o bloco de operadores do
    período; pular é o inverso; Mindslaver = turno do oponente vira seu.
  - `terminal` — predicados de vitória/derrota/empate (CR 104).
  - `adversarial` (aprox.) — escolha do oponente por minimax.
  - `noop` — sem efeito em Commander (wishes, ante, planechase).

Ressalvas: isto mede se o *tipo* de habilidade cabe na álgebra, não se a
magnitude/condição foi lida (Fase 2) nem se o operador prevê o Forge (Fase 3).
As classes novas têm **valor diferido** (tabela em `ALGEBRA_COVERAGE.md`): um
Clone só vale algo quando a Fase 5 resolver o que ele copia; `terminal` depende
da decisão 8.1 (recurso de vitória).

### Fase 2 — Operadores com magnitude e dominância (≈ 1–2 semanas)

- [x] Adicionar magnitude, frequência e condição ao schema
      (`src/ontology/schema.py` hoje não guarda quantidade).
      → `src/operators/compile.py` (`Operator`/`Step`: magnitude, alvo,
      condições, forma, frequência, velocidade, custo).
- [x] Compilar cada carta em operadores a partir do Forge. SVars são
      resolvidas por conteúdo (Clone, Charm, Repeat), faces alternativas
      (aventura, split, transformação) compiladas junto. Invólucros genéricos
      (Animate, Play, MayPlay, Moved, ReplaceEffect) com parâmetros lidos em
      todas as cartas menos 169 (0,5%), que ficam fora da comparação.
      Pendente: fallback `patterns.py` para as 131 cartas sem script.
- [x] **Ordem parcial de dominância** dentro da mesma assinatura: A ≥ B se
      cmc ≤, pips ⊆ ou mais flexíveis, velocidade ≥, magnitude ≥, sem
      condições extras. → `src/operators/dominance.py`,
      `scripts/build_dominance.py`, `data/ontology/dominance_v1.json`.
      **Mudança em relação ao plano:** Commander é singleton, então cartas
      dominadas não saem do pool — B espera enquanto o dominador A estiver
      disponível e fora do deck (no MIQP da Fase 7: `x_B ≤ x_A`). Subtipos ficam
      fora da assinatura (exceto tipos básicos de terreno e Aura/Equipment/…);
      quando diferem e só B é da tribo do comandante, B não espera.

**Aceite:** pares canônicos corretos (Lightning Bolt > Shock;
Counterspell > Cancel); amostra de 100 relações conferida à mão.
→ **Pares canônicos corretos** (mais 4 pares que têm de sair incomparáveis).
2.291 relações estritas, 920 cartas dominadas, 701 pares equivalentes.
Conferência manual: a primeira amostra revelou 8 erros sistemáticos
(custo adicional ignorado, tipo básico de terreno fora da assinatura, condição
em desvantagem com polaridade invertida, SVars não lidas, variável com sinal,
segunda face ignorada, sinal do pump, `Mode$` descartado), todos corrigidos e
com teste em `tests/test_operators.py`. Depois das correções: 1 erro em 100 na
amostra da semente 7 e 1 em 100 numa amostra independente (semente 11), ambos
corrigidos. A amostra final está em `data/ontology/DOMINANCE.md` para a sua
conferência.

### Fase 2.5 — Relógio de dano analítico (≈ 1 semana)

Vem logo depois da Fase 2 porque só precisa de magnitudes (poder, cmc, mana
produzida); **não depende da Fase 3**. Detalhes na seção 2.6.

- [x] Curva de mana `m_t` a partir de terrenos (modelo de terrenos da Fase 0)
      e ramp, por hipergeométrica sobre a ordem de compra. → esperança estimada
      por amostragem de ordens de compra (a política de lançamento não tem forma
      fechada); ordem fixa para testes determinísticos. `src/operators/clock.py`.
- [x] Política de lançamento: no turno `t`, lançar o conjunto de maior impacto
      que cabe em `m_t` (mochila pequena por turno). Impacto medido no próprio
      relógio (dano futuro em equivalente de poder), não em `V`.
- [x] Dano por turno com enjoo de invocação, `P_conectar` e capacidade de
      bloqueio `b_t` por oponente (taxa-base do catálogo). → `b_t` é um
      estoque: a mesa ganha bloqueadores a uma taxa por turno e o bloqueio chump
      gasta o bloqueador (bloqueadores eternos zeravam o dano de qualquer deck).
- [x] Modificadores de 2ª ordem: trample/evasão sobre `P_conectar`, overrun
      como multiplicador, haste removendo o enjoo. Mais: anthem, Craterhoof
      (+X por criatura), fábricas de token por virar (Krenko), equipamentos e
      auras (voltron), tokens de ETB e por manutenção.
- [x] Relógios paralelos para os outros terminais (veneno, dano de
      comandante, biblioteca, vitória alternativa). → Na primeira entrega só
      havia combate, veneno, comandante e mill/vitória **incondicionais** ao
      resolver; marcado como feito antes da hora. Completo na Fase 2.5b abaixo.
- [x] **Fase 2.5b — todas as rotas de vitória.** O relógio virou uma máquina
      de eventos (criatura morre, mágica lançada, landfall, compra, ganho de
      vida, perda de vida do oponente, token criado/sacrificado, manutenção):
      dano direto e drenos tiram dos mesmos 120 pontos do combate; outlets de
      sacrifício (inclusive sem efeito próprio, como Viscera Seer, e por tipo,
      como Siege-Gang); quantidades lidas do estado (devoção do Gray Merchant);
      mill por gatilho e "jogador-alvo mói N" decidido pelo plano (oponentes ou
      a própria biblioteca, se o deck tem vitória por biblioteca vazia); loops
      sem limite (Sanguine Bond + Exquisite Blood) drenam a mesa. Vitória
      alternativa e "oponentes perdem" com a condição do script avaliada no
      estado (`Count$` do Forge): Gates de nomes diferentes ≥ 10, biblioteca ≤
      devoção, comprar com a biblioteca vazia, segunda conjuração do Approach,
      vida ≥ 40, domínio + cores. A política segura uma vitória condicional até
      ela valer. Nossa vida também cai a uma taxa-base (3/turno), senão o
      Felidar venceria na primeira manutenção (começamos com 40).
      Efeitos condicionais que o avaliador não lê continuam não contando.
- [x] Métricas: turno médio e variância do relógio, dano até T6/T8, mana
      desperdiçada por turno. → `scripts/clock_report.py` → `data/ontology/CLOCK.md`.

**Aceite:**
- Ramp × topo de curva aparece como interação positiva sem palavra em comum
  (o exemplo do 7/7 dá ≈ 2 ataques a mais).
- O valor de um overrun cresce com o número de criaturas do deck.
- Num deck de criaturas verdes, trocar 10 criaturas grandes por 10 cartas de
  "draw" sem conversor em dano piora o relógio.

**Resultado (2026-10-05): ACEITE**, com cartas reais compiladas. Pelakka Wurm +
2 Rampant Growth: syn = +14 de dano = **2,0 ataques a mais**. Ganho do Overrun:
+0,22 → +1,31 de dano até T8 de 5 para 35 criaturas. Deck verde montado do
catálogo sem escolha manual: trocar as 10 criaturas mais fortes lançáveis por
10 draws cai de 28,0 para 22,3 de dano até T8, e a conclusão vale nas 9
combinações de taxa de bloqueio × `P_conectar` testadas. Achado: a primeira
versão do teste trocava criaturas de 8–9 manas e o draw ganhava — o modelo
está certo, ameaças que não entram até T12 valem menos que compra barata.
Decks do repositório: o Lightning gerado causa 17,0 de dano até T8 contra 28,4
do Lightning humano; Krenko gerado é o mais rápido (T≈10) pelo próprio Krenko,
não pelas 99. Custo: ~0,35 ms por partida (≈ 20 ms por deck com 50 amostras).
Limites: oponentes só bloqueiam (sem remoção nem corrida), habilidades
ativadas que não sejam mana/token não contam, contagens variáveis aproximadas.

**Resultado 2.5b (2026-10-06): ACEITE** (critério 4 do `CLOCK.md`, decks de 99
com cartas reais e o resto neutro): os payoffs de morte multiplicam o dano do
aristocrats por 3,5× e metade dele vem de fora do combate; o spellslinger tira
2/3 da vida por dano direto; o Maze's End vence por vitória alternativa em 14%
das partidas até T15 com 15 Gates e nunca com 3; o deck de mill progride só
pela biblioteca; com Oracle/Maniac/Jace no deck as cartas "jogador-alvo mói"
passam a moer a própria biblioteca; o loop Sanguine + Exquisite fecha 9% das
partidas pela vida e só o Bond nenhuma. Testes determinísticos em
`tests/test_clock.py` cobrem Maze's End (nomes diferentes), aristocrats, o
loop, Laboratory Maniac, Thassa's Oracle (espera a devoção cobrir a
biblioteca), Approach e Guttersnipe. Cobertura: das 84 cartas legais com
`WinsGame`/`LosesGame`, o relógio avalia 28 (33%); as outras não prometem
nada. Limites: terrenos que entram virados não são modelados (Maze's End e
Gates ficam um pouco rápidos); Hermit Druid, Mesmeric Orb, Door to Nothingness
e mill de "metade da biblioteca" por repetição ainda não são lidos.

### Fase 3 — Forge como oráculo: micro-experimentos (≈ 2–3 semanas)

- [x] Harness que gera estados de Puzzle (ou chama a API Java), executa uma
      ação ou passa um turno, e lê o vetor de recursos resultante.
      → `tools/forge_oracle/ForgeOracle.java`: monta o estado com
      `forge.game.GameState` (formato do Puzzle Mode), resolve a habilidade só
      como efeito (custo comparado à parte), roda as ações baseadas em estado e
      imprime o vetor (vida, veneno, mão, biblioteca, cemitério, exílio, mana,
      permanentes por tipo, poder, tokens, contadores +1/+1, virados) antes e
      depois. Forge compilado do checkout local (mesmo commit dos scripts) com o
      JDK 21 do PyCharm e Maven 3.9.16.
- [~] Medir Δ para cartas isoladas e pares amostrados. Cartas isoladas: sim.
      **Pares: pendente** — o preditor ainda não modela 2ª ordem (anthem que
      afeta o token criado, Doubling Season); isso entra com a Fase 5.
- [x] Comparar com a previsão dos operadores abstratos: métrica de
      **fidelidade**. → `src/operators/predict.py` (Δ previsto só a partir dos
      operadores) e `scripts/forge_oracle.py` → `data/ontology/ORACLE_FIDELITY.md`.

**Aceite:** erro de previsão do Δ ≤ 10% em ≥ 85% dos casos de 1ª ordem.
**Parada:** se não for possível dirigir o Forge programaticamente, reduzir o
oráculo a um conjunto de casos validados manualmente.

**Resultado (2026-10-05): ACEITE.** Primeira rodada: 78,7% (1.129 casos). Os
erros mostraram lacunas reais da abstração — `Defined$ TargetedController`
(o controlador do alvo ganha a vida / recebe o token), restrições de alvo
(atacante, com voar, mana value), filtros de descarte, exilar em vez de morrer,
token artefato-criatura ou virado, "cada jogador", habilidades vindas de
palavra-chave — todas corrigidas e com teste (`tests/test_predict.py`).
Desenvolvimento (semente 3): 97,4%. **Avaliação separada** (semente 11, sem as
1.095 cartas do desenvolvimento): **98,7% (370/375)**. Escopo medido: 3.537
cartas com efeito de 1ª ordem e quantidades numéricas (~11% das legais);
gatilhos, estáticos e 2ª ordem ainda não são medidos.

### Fase 4 — Preços latentes π e força de carta (≈ 1–2 semanas)

- [ ] Montar `C_k`, `E_k` para todas as cartas abstraíveis.
- [ ] Ajustar `π*` (Huber, `π_mana = 1`, efeito de era).
- [ ] Ranking de força `s_k`; validação fora da amostra (cmc de cartas
      retidas) e intervalos via bootstrap.
- [ ] Ajustes de Commander derivados das regras: "each opponent" ×3, vida 40,
      dano de comandante.

**Aceite:** previsão de cmc fora da amostra melhor que baseline de média por
tipo; Mind's Eye e similares com `s_k` claramente negativo.

### Fase 5 — Grafo e V(D) (≈ 2 semanas)

- [ ] Construir `c` (valor isolado) e `W` (arestas tipadas com taxa e
      probabilidade de coexistência).
- [ ] Operadores de 2ª ordem como escalares/máscaras sobre `W`.
- [ ] Terrenos como operadores de mana comparados contra o básico (substitui a
      Fase 0 no modelo).
- [ ] `V(D)` analítica e decomposição explicável por aresta, com o relógio de
      dano da Fase 2.5 como numerário.

**Aceite:**
- `V` reprova os decks atuais de Krenko e Lightning em relação às versões
  reconstruídas; a decomposição explica cada carta.
- **Teste anti-viés de engine:** com o pedido "verde, criaturas", o sistema
  monta um stomp coerente (ramp, ameaças grandes, trample/overrun) sem que
  "stomp" esteja escrito em lugar nenhum, e esse deck é competitivo em `V`
  com decks de engine da mesma cor. Se `V` só gostar de engines, `V` está
  errada.

### Fase 5.5 — Rotas e descritores de arquétipo (≈ 1–2 semanas)

Detalhes nas seções 2.7 e 6.1. Depende das Fases 2, 2.5 e 4.

- [ ] Grafo de recursos do catálogo: nós = recursos/eventos/terminais,
      arestas = operadores com taxa `π`.
- [ ] Enumeração de rotas mana → terminal (caminhos simples até comprimento
      `k`, mais ciclos), restrita à identidade de cor quando houver
      comandante.
- [ ] Métricas por rota: suporte (aresta gargalo), relógio, fragilidade.
- [ ] Descritores de postura (velocidade, interação, valor, restrição,
      doação, ordem) e motor dominante para qualquer deck.
- [ ] Catálogo de rotas em `eval/routes/` com as cartas de cada aresta.

**Aceite:**
- Recupera as rotas conhecidas (go-wide, stomp, voltron, aristocrats,
  spellslinger, mill, infect, vitória alternativa, combo) **sem** que elas
  tenham sido listadas.
- Os descritores separam posturas conhecidas: listas de referência de
  aggro, midrange e controle caem em regiões distintas do espaço (a lista de
  referência serve só para o teste, nunca como sinal de treino).
- Para cada comandante de teste, lista as rotas viáveis em ordem de relógio,
  com ao menos uma candidata à descoberta para a Fase 9.

### Fase 6 — Loops espectrais (≈ 1–2 semanas)

- [ ] Montar `G` sobre operadores repetíveis.
- [ ] Busca de hiperarestas com `ρ ≥ 1` (combos) e `ρ` alto (engines),
      restrita à identidade de cor.
- [ ] Validar combos encontrados no oráculo (Fase 3).

**Aceite:** recupera combos conhecidos de 2–3 cartas **sem** que eles tenham
sido fornecidos; cada combo novo confirmado no Forge.

### Fase 7 — Otimizador MIQP + busca local (≈ 1–2 semanas)

- [ ] Formulação da seção 6 em OR-Tools/SCIP.
- [ ] Busca local para os termos não lineares.
- [ ] Substituir `fill()` gulosa; o LLM vira proponente/explicador.

**Aceite:** para um mesmo `V`, o ótimo do MIQP ≥ greedy em todos os
comandantes de teste; solução em < 1 min.

### Fase 8 — Interação e meta endógeno (≈ 2–3 semanas)

- [ ] Grafos adversários a partir da população gerada.
- [ ] Valor de negação de cada resposta (seção 4).
- [ ] Coevolução: otimizar contra a população e atualizá-la.

**Aceite:** o número e o tipo de respostas convergem sem quota fixa; nenhum
deck final com zero respostas a uma classe presente na população.

### Fase 9 — Exploração e avaliação (≈ 2–3 semanas)

Detalhes na seção 6.1.

- [ ] **MAP-Elites** com células = rota dominante × motor × descritores de
      postura (Fase 5.5).
- [ ] **Novelty search** com arquivo de comportamentos; combinação com o
      MAP-Elites.
- [ ] **Novidade (a posteriori):** só aqui a popularidade entra, para medir se
      as elites, rotas e combos encontrados são pouco usados.
- [ ] Teste humano: os decks no EDHPlay e na mesa.

**Aceite:** o arquivo cobre todas as rotas viáveis da Fase 5.5 e as posturas
principais (aggro, midrange, controle, combo, stax); existem células com
elite competitiva e inclusão observada baixa.

**Figura central candidata:** força funcional (`V`, `ρ`) × inclusão
observada. As soluções fortes e raras são a contribuição.

### Fase 10 — Modelo fundacional (contínua, depois da Fase 9)

Detalhes na seção 6.2.

- [ ] **Codificador de cartas** autossupervisionado sobre scripts do Forge +
      oracle (texto → operador, operador mascarado, previsão de custo).
- [ ] **Surrogate de `V`** (Deep Sets / Set Transformer) treinado no arquivo
      do MAP-Elites; filtra candidatos antes da avaliação analítica.
- [ ] **Ciclo busca → treino:** gerador de decks treinado no arquivo, usado
      como proposta para a busca seguinte.
- [ ] **Modelo de mundo** a partir dos micro-experimentos da Fase 3 (o
      componente mais caro; só depois dos outros três).
- [ ] Validação no Forge **fora** do laço a cada iteração (proteção contra
      Goodhart).

**Aceite:**
- O codificador avalia cartas de uma coleção **não vista no treino** com
  erro de previsão de custo comparável ao das cartas vistas.
- O surrogate ordena decks como a `V` analítica (correlação de postos alta)
  a uma fração do custo.
- Cada iteração do ciclo melhora o arquivo (mais células ocupadas ou elites
  melhores) **e** a melhora se mantém na validação do Forge.

## 8. Decisões em aberto

1. **Numerário e objetivo** — proposta (2026-10-04): **dano com relógio**
   (seção 2.6); outros recursos convertem para dano por `π`. Veneno, dano de
   comandante, biblioteca e vitórias alternativas como relógios paralelos,
   com `V` usando o mais rápido. Falta confirmar.
2. **Horizonte `T` e desconto `γ`.**
3. **Âncora de π:** só a curva de design, ou com parâmetros normativos e
   análise de sensibilidade?
4. **Granularidade da álgebra:** quantos recursos e eventos? (decidido pela
   Fase 1.)
5. **Interface com o Forge:** Puzzle files ou API Java direta?
6. **Destino do código atual:** embeddings ficam só como recuperação de
   candidatos (recall), nunca como score de sinergia.

## 9. Riscos

- **Viés pró-engine:** um objetivo feito só de sinergia premia engines e
  ignora arquétipos de primeira ordem (stomp, aggro de stats). Mitigação: `V`
  com relógio de dano (seção 2.6) e o teste anti-viés da Fase 5.
- **`P_conectar` é um parâmetro até a Fase 8.** O valor de stomp é sensível a
  ele. Mitigação: análise de sensibilidade em `b_t` e reportar a faixa em que
  o deck continua ótimo.
- **Goodhart no ciclo de autoaperfeiçoamento:** o gerador aprende as falhas
  de `V`. Mitigação: validação no Forge fora do laço (seção 6.3).
- **Fidelidade da abstração:** camadas, substituição, cópia e escolhas ficam
  fora ou aproximados. Mitigação: medir cobertura (Fase 1) e fidelidade
  (Fase 3) e reportar.
- **π herda o julgamento da WotC.** Mitigação: análise de sensibilidade e
  âncoras independentes (terminal, oráculo).
- **Explosão de hiperarestas:** limitar a operadores repetíveis e à identidade
  de cor; podar por `π`.
- **Custo do Forge:** micro-experimentos são curtos, mas ainda caros. Usar só
  para validar e calibrar, nunca dentro do laço do otimizador.

## Referências

- A. Churchill, S. Biderman, A. Herrick. *Magic: The Gathering is Turing
  Complete*. arXiv:1904.09828, 2019.
- W. Leontief. *The Structure of American Economy, 1919–1929*. 1941 (modelo
  insumo-produto).
- D. Hawkins, H. A. Simon. *Note: Some Conditions of Macroeconomic
  Stability*. Econometrica 17(3–4), 1949.
- A. Bhatt et al. *Exploring the Hearthstone Deck Space*. FDG 2018
  (MAP-Elites para construção de decks).
- M. Fontaine et al. *Mapping Hearthstone Deck Spaces through MAP-Elites with
  Sliding Boundaries*. GECCO 2019.
- J.-B. Mouret, J. Clune. *Illuminating search spaces by mapping elites*.
  arXiv:1504.04909, 2015 (MAP-Elites).
- J. Lehman, K. O. Stanley. *Abandoning Objectives: Evolution Through the
  Search for Novelty Alone*. Evolutionary Computation 19(2), 2011.
- J. Schrittwieser et al. *Mastering Atari, Go, Chess and Shogi by Planning
  with a Learned Model*. Nature 588, 2020 (MuZero: modelo de mundo aprendido).
- M. Zaheer et al. *Deep Sets*. NeurIPS 2017; J. Lee et al. *Set
  Transformer*. ICML 2019 (funções de conjunto para `V(D)`).
- Forge — modo de simulação headless:
  <https://github.com/Card-Forge/forge/wiki/ai>
- Forge — Puzzle Mode (formato de estado):
  <https://www.slightlymagic.net/forum/viewtopic.php?p=214490>
