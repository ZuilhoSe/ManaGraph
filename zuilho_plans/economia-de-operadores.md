# Economia de operadores — sinergia deduzida das regras

> Status: proposta de direção (2026-10-02). Substitui a ideia de "sinergia =
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

## 7. Passo a passo

Cada fase tem um entregável, um critério de aceite e um critério de parada
(quando a hipótese falhou e é preciso repensar).

### Fase 0 — Consertar o que já está errado (≈ 2–4 dias)

Independe do resto e reduz o ruído das comparações futuras.

- [ ] Terrenos: básicos na proporção dos pips (Karsten, `hypergeometric.py`)
      como default. Não-básicos entram **só por troca** com
      `Δ = ΔP(fontes corretas em t) − custo(entra virado) − custo(drawback) + utilidade > 0`.
- [ ] Remover `land_urgent` para não-básicos e o cosseno com o comandante para
      terrenos.
- [ ] Drawbacks de terreno como predicados (dá mana a oponente, sacrifica
      terrenos, cor condicional a tipo).
- [ ] `_ontology_pair_score` alinhado ao `ONTOLOGY.md`: contagens com `min`
      saturante.

**Aceite:** o Lightning reconstruído tem ≥ 20 básicos e nenhum terreno que
gere mana para oponentes ou seja de outra tribo.

### Fase 1 — Auditoria de cobertura do Forge (≈ 1 semana)

- [ ] Inventariar os tipos de habilidade nos scripts (`A:`, `T:`, `S:`, `R:`,
      `SVar:`), com seus `Mode$`, `ApiType`, `ValidCard$`, `Execute$`.
- [ ] Definir a **álgebra mínima**: recursos R, eventos, flags F, operadores de
      1ª e 2ª ordem.
- [ ] Mapear cada tipo de habilidade para a álgebra, ou marcar como fora dela.
- [ ] Relatório: % de cartas totalmente abstraíveis, famílias fora (layers,
      controle, cópia, alternate win, escolhas), top-N cartas que mais quebram.

**Aceite:** ≥ 80% das cartas legais em Commander totalmente abstraíveis.
**Parada:** < 60% → repensar a granularidade da álgebra antes de seguir.

### Fase 2 — Operadores com magnitude e dominância (≈ 1–2 semanas)

- [ ] Adicionar magnitude, frequência e condição ao schema
      (`src/ontology/schema.py` hoje não guarda quantidade).
- [ ] Compilar cada carta em operadores a partir do Forge, com fallback
      `patterns.py` para não mapeadas.
- [ ] **Ordem parcial de dominância** dentro da mesma assinatura: A ≥ B se
      cmc ≤, pips ⊆ ou mais flexíveis, velocidade ≥, magnitude ≥, sem
      condições extras. Cartas dominadas saem do pool.

**Aceite:** pares canônicos corretos (Lightning Bolt > Shock;
Counterspell > Cancel); amostra de 100 relações conferida à mão.

### Fase 3 — Forge como oráculo: micro-experimentos (≈ 2–3 semanas)

- [ ] Harness que gera estados de Puzzle (ou chama a API Java), executa uma
      ação ou passa um turno, e lê o vetor de recursos resultante.
- [ ] Medir Δ para cartas isoladas e pares amostrados.
- [ ] Comparar com a previsão dos operadores abstratos: métrica de
      **fidelidade**.

**Aceite:** erro de previsão do Δ ≤ 10% em ≥ 85% dos casos de 1ª ordem.
**Parada:** se não for possível dirigir o Forge programaticamente, reduzir o
oráculo a um conjunto de casos validados manualmente.

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
- [ ] `V(D)` analítica e decomposição explicável por aresta.

**Aceite:** `V` reprova os decks atuais de Krenko e Lightning em relação às
versões reconstruídas; a decomposição explica cada carta.

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

### Fase 9 — Avaliação e paper

- [ ] **MAP-Elites** com descritores mecânicos (cmc médio, proporção de
      criaturas, recurso dominante em `G`, ρ máximo). Guarda o melhor deck por
      célula.
- [ ] **Novidade (a posteriori):** só aqui a popularidade entra, para medir se
      as elites e os combos encontrados são pouco usados.
- [ ] Teste humano: os decks no EDHPlay e na mesa.

**Figura central candidata:** força funcional (`V`, `ρ`) × inclusão
observada. As soluções fortes e raras são a contribuição.

## 8. Decisões em aberto

1. **Numerário e objetivo:** qual é o recurso de vitória? Dano até 120?
   Vetor ponderado?
2. **Horizonte `T` e desconto `γ`.**
3. **Âncora de π:** só a curva de design, ou com parâmetros normativos e
   análise de sensibilidade?
4. **Granularidade da álgebra:** quantos recursos e eventos? (decidido pela
   Fase 1.)
5. **Interface com o Forge:** Puzzle files ou API Java direta?
6. **Destino do código atual:** embeddings ficam só como recuperação de
   candidatos (recall), nunca como score de sinergia.

## 9. Riscos

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
- Forge — modo de simulação headless:
  <https://github.com/Card-Forge/forge/wiki/ai>
- Forge — Puzzle Mode (formato de estado):
  <https://www.slightlymagic.net/forum/viewtopic.php?p=214490>
