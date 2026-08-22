# Plano: modo de testes / eval harness pro Architect+Solver

Sessão de origem: debug de runs reais em 2026-08-20 (Aminatou, Veil Piercer — deck de
encantamentos). Contexto completo nos logs dessa sessão, resumido abaixo. Este arquivo
não é rastreado pelo git (`lucas_plans/` está no `.gitignore`) — é anotação de trabalho,
não documentação do projeto.

## Os 3 problemas que motivaram isso

Todos observados no mesmo run real (prompt: "remova ~15 cartas ruins/pouco sinérgicas,
troque por counters e engines de draw"):

**Problema A — `cut()` desfaz o que o Architect acabou de escolher, na mesma rodada.**
Assim que `n_stripped > 0` (qualquer carta removida por preço/nome desconhecido/etc),
o Solver popula `candidate_pool` e roda até 12 trocas via `cut()` — uma varredura cega
de "essa carta do pool pontua melhor que minha pior carta atual?", sem relação com o
que foi de fato stripped. No log: o Architect adicionou `Predict` e `Opt` deliberadamente
nessa rodada; o `cut()`, na mesma passada de `solve()`, trocou `Predict` → `Research
Assistant` e `Opt` → `Hunter of Eyeblights`. Também trocou `Dovin's Veto` (que o próprio
Architect chamou de "the best counterspell in the colors" no mesmo turno) por
`Northampton Farm`, e um land (`Caves of Koilos`) por um spell (`Assassin's Strike`) sem
proteção estrutural de contagem de mana no meio do processo.
Local: `src/solver.py`, função `cut()` (~linha 256) e o gate em `solve()` (~linha 489:
`if deck.slot_count() == MAIN_DECK_SIZE and deck.candidate_pool:`).

**Problema B — candidate_pool não tem noção do arquétipo do deck.**
O retrieval que alimenta `fill()`/`cut()` vem do texto cru do prompt do usuário + 5
queries genéricas fixas (`ROLE_QUERIES` em `solver.py`: "add mana ramp artifact", "draw
a card", "destroy target exile counter", "creature tokens", "basic land"). Nenhuma delas
sabe que o deck é "enchantments matter" — por isso cartas como `Foundry Assembler`
(artefato), `Vampire Hexmage`, `Mausoleum Guard`, `The Mana Rig`, `Reef Worm` entram só
por pontuarem bem contra queries genéricas, sem nenhuma relação com o plano do deck.

**Problema C — Architect inventa sinergia que a carta não tem.**
Substituiu `Entreat the Angels` por `The Watcher in the Water` com a razão "draws cards
specifically when you scry/surveil" — mas o oracle_text real da carta (já visto em log
anterior na sessão) é sobre criar Tentacle tokens quando você compra no turno do
oponente; não menciona surveil/scry em lugar nenhum. Alucinação de justificativa.

## Por que não dá pra simplesmente "consertar e ver se ficou bom"

Rodar o grafo inteiro ponta a ponta pra validar cada mudança é caro e lento (chamadas
de Architect já vistas entre 1 e 15 minutos nessa sessão, às vezes travando em retry de
429) e não-determinístico (mesmo prompt, resultado pode variar entre runs). Não dá pra
usar isso como critério de "melhorou ou piorou" em iteração rápida.

## A ideia: 3 camadas, da mais barata pra mais cara

Cada problema pede um tipo de avaliação diferente — não existe "um modo de teste" único
que sirva pros 3.

### Camada 1 — Testes de regressão determinísticos (resolve o Problema A)

Isso é lógica pura do `Solver`, sem LLM envolvido nenhum. Mesmo padrão que já usamos
nessa sessão pro bug do `_legality_reason` (ver `tests/test_stage2.py`,
`test_strip_illegal_catches_freshly_substituted_over_price_card` e
`test_strip_illegal_exempts_baseline_over_price_card` como referência de estilo).

O que fazer:
- Montar um `DeckState` fixture reproduzindo o cenário exato: deck com N cartas, uma
  substituição já aplicada (`deck.cards` já reflete `Predict` dentro, por exemplo),
  1+ carta marcada pra strip (preço/nome desconhecido), `candidate_pool` populado.
- Chamar `solver.solve(deck, ...)` (ou `cut()` isolado) diretamente.
- Afirmar que a carta recém-substituída (`Predict`, no cenário do log) **sobrevive** —
  não é candidata a saída do `cut()` na mesma passada em que entrou.
- Cenário adicional: swap de land por não-land dentro do `cut()` não deve acontecer sem
  alguma checagem de contagem de mana (ou, no mínimo, testar que isso é *intencional* e
  documentado, não um acidente de scoring).

Custo: ~1-2h de trabalho, zero custo de LLM, roda em milissegundos, protege pra sempre.
Esse é o único item das 3 camadas que eu chamaria de "correção", não "avaliação" — os
outros dois exigem decidir *o quanto* de comportamento mudar, não só corrigir um bug.

Arquivo: `tests/test_stage2.py` (ou um novo `tests/test_cut_stability.py` se a lista
de casos crescer bastante).

### Camada 2 — Benchmark offline de qualidade de retrieval (resolve o Problema B)

Sem LLM — só o índice de embeddings local (Chroma), que já é rápido e determinístico
(mesma query, mesmo resultado).

O que fazer:
1. Montar um pequeno conjunto fixo de arquétipos de teste (5-8 pra começar), cada um com:
   - commander + identidade de cor (pode reusar Aminatou/Esper enchantments como o
     primeiro caso, já que é o que gerou o problema).
   - uma lista curada à mão de "cartas boas pra esse arquétipo" (staples conhecidos —
     dá pra puxar de uma página de EDHREC do arquétipo, ou montar manualmente).
   - uma lista de "cartas claramente fora de tema" (pode ser justamente as que
     apareceram nesse log: Foundry Assembler, Vampire Hexmage, etc., pra esse caso
     específico de Aminatou).
2. Escrever um script (`scripts/eval_retrieval.py` ou similar) que roda o mesmo
   retrieval que o Solver usa (`_seed_pool_from_retrieve` / `search_cards`) contra cada
   arquétipo do benchmark, e calcula uma métrica tipo:
   - `% do candidate_pool retornado que está na lista "boa"`
   - `% que está na lista "ruim"` (idealmente ~0%)
   - opcional: precision@10/@20 se quiser algo mais formal.
3. Rodar esse script **antes** de mexer em `ROLE_QUERIES`/scoring, salvar o número.
   Mexer no que for necessário (queries mais específicas do tema, pesar arquétipo/tribal
   tags se existirem no catálogo, etc.). Rodar de novo, comparar.

Custo: ~meio dia pra montar o benchmark inicial (a parte manual é curar as listas
boas/ruins por arquétipo), depois é reuso — cada rodada do script leva segundos.

Onde colocar: um novo diretório `eval/` (ou `benchmarks/`) na raiz do projeto — dados de
benchmark (JSON/YAML por arquétipo) + o script. Não precisa ser package Python formal.

### Camada 3 — Replay de prompts reais ponta a ponta (resolve o Problema C)

O único que exige LLM de verdade — por isso o mais caro, lento e não-determinístico.
Não vira gate automatizado de CI; é uma checagem manual antes de mudanças maiores no
prompt do Architect.

O que fazer:
- Manter uma pasta com uns 5-10 prompts "golden" — prompts reais já rodados nessa sessão
  (dá pra reaproveitar os desses logs) + os decks de entrada correspondentes.
- Rodar esses prompts ponta a ponta antes de mexer no prompt/system message do Architect,
  salvar o `deck_diff` final de cada um (não precisa salvar o texto todo, só
  entrada→saída) como baseline.
- Depois de mexer, rodar de novo, comparar os diffs manualmente (não precisa de
  automação sofisticada — é "será que o novo prompt ainda produz coisa razoável nesses
  casos conhecidos, ou quebrou algo").

Adicional mais barato pro Problema C especificamente (não é bem "modo de teste", é uma
salvaguarda em runtime): já que o catálogo tem o `oracle_text` real de cada carta
sugerida, dá pra cruzar a `reason` que o Architect escreveu contra o texto real da carta
— se a razão menciona uma keyword de mecânica (ex: "surveil", "scry", "token") que não
aparece no oracle_text real, sinalizar como suspeita. Isso é um lint determinístico
rodando dentro do próprio pipeline (Inventory ou Solver node), não uma comparação
antes/depois — mas resolve o caso concreto sem precisar de outro LLM julgando semântica.

## Ordem sugerida de trabalho

1. Camada 1 primeiro — resolve o Problema A de vez, barato, baixo risco, sem
   dependência das outras duas.
2. Camada 2 em seguida — dá um número objetivo pra iterar no Problema B sem feeling.
3. Camada 3 por último, e só quando for mexer de fato no prompt do Architect (não é
   bloqueante pras outras duas).
4. O lint de reason-vs-oracle_text (parte do Problema C) pode ser feito em paralelo com
   a Camada 1, já que também é puramente determinístico — só depende de decidir onde no
   pipeline ele entra (Inventory node parece o lugar natural, já que é onde as cartas do
   delta são resolvidas contra o catálogo).

## Decisões em aberto (discutir na próxima sessão)

- Camada 2: de onde tirar as listas "boas"/"ruins" por arquétipo — curadoria manual pura,
  ou dá pra puxar algo de EDHREC/outra fonte pra acelerar? Quantos arquétipos são
  suficientes pra confiar no benchmark sem virar trabalho manual demais?
- Camada 1: vale também cobrir a lacuna de "cut() trocando land por não-land sem
  proteção de contagem de mana" como um teste, ou isso é comportamento aceitável e só
  faltou documentar?
- Se/quando vale formalizar a Camada 3 com mais automação (ex: diff estruturado do
  deck em vez de leitura manual), ou se leitura manual dá conta pro volume de mudanças
  no prompt do Architect.

## Sessão 2026-08-21 — resultado

### Camada 1 — feito

Implementado e testado:
- `src/solver.py`: `_freshly_touched_names()` lê `deck.last_delta` (added/substituted-in);
  `cut()` passa esse conjunto pra `_worst_cut()` no loop de swap, que agora pula qualquer
  carta recém-tocada nessa rodada como candidata a vítima.
- `src/deck_state.py`: `from_dict()` estava descartando `last_delta` silenciosamente em todo
  round-trip entre nós do grafo — esse era o segundo bug que fazia a proteção acima não ter
  efeito nenhum mesmo se implementada. Corrigido (`last_delta=dict(data.get("last_delta") or {})`).
- `tests/test_stage2.py`: 2 testes novos — um controle (prova que o bug é real sem a
  proteção) e um de regressão (prova que a proteção segura). 68/68 testes passando.
- Achado bônus: `last_delta` também é lido pelo prompt do `InventoryAgent`
  (`src/inventory_agent.py`, "Collect every card name in last_delta") — o fix do
  `from_dict()` corrige de graça um bug latente ali também, não só no `cut()`.
- Escopo cortado por pedido explícito: proteção de land-por-não-land no swap do `cut()`
  ficou de fora. Fica pra quando existir uma solução melhor de contagem de land (mesmo
  projeto do calculador hipergeométrico de land/source discutido em outra sessão).

### Camada 2 — parcial: benchmark de retrieval feito, benchmark de seleção ainda não

O que foi feito — só a metade "retrieval" (o que entra no `candidate_pool`):
- `eval/archetypes/aminatou_esper_enchantments.json`: primeiro arquétipo. Commander
  Aminatou, Veil Piercer; query final usada: "Quero 5 engines de draw e 5 counterspells
  para esse deck esper de encantamentos." Lista boa e ruim curadas à mão por Lucas.
- `scripts/eval_retrieval.py`: roda `DeckSolver._retrieve()` isolado (sem LLM, sem deck
  populado — `_retrieve()` não lê `deck.cards`) contra cada arquétipo, mede recall na
  lista boa e taxa de acerto na lista ruim.

Baseline medido (estado atual do sistema, nenhuma correção de retrieval feita ainda):
```
candidate_pool size: 270
good recall: 1/10 (10%)   -- só "Counterspell" apareceu
bad rate:    9/10 (90%)
```
Mesmo com query bem específica (arquétipo + o que exatamente se quer), o recall não
melhora -- confirma que o problema é o `ROLE_QUERIES` genérico dominando o retrieval,
não a query do usuário ser vaga.

O que ficou faltando (não é bug, é trabalho ainda não feito):
- Benchmark de **seleção/scoring** (a outra metade do Problema B): dado um
  `candidate_pool` contaminado e um deck real de ~96 cartas, será que `score_candidate`/
  `fill()`/`cut()` ainda escolhe as cartas ruins, ou já filtra bem com contexto real?
  Isso não está medido — só sabemos que aconteceu uma vez, no run real de 2026-08-20.
  Fixture já preparado pra isso: `eval/scenarios/aminatou_esper_enchantments_full_deck.json`
  (deck completo + query original do log, mesmo schema `{query, deck}` do import/export).
  Falta decidir a lista boa/ruim desse benchmark (discutimos que pode ser diferente da
  lista de retrieval, já que aqui o julgamento é "dado esse deck específico", não "dado
  só o comandante") e escrever o script.
- Nenhuma correção de fato em `ROLE_QUERIES`/scoring foi feita. O baseline 10%/90% acima
  é o estado atual, não um "antes" de uma mudança já aplicada.
- Só 1 arquétipo no benchmark até agora -- não dá pra confiar no número como
  representativo de todos os tipos de deck ainda.

### Fora do escopo do harness, mas feito na mesma sessão

- Feature de import/export de condição inicial (query+deck+configs): `--import`/
  `--export`/`--dry-run` no CLI (`src/main_agent.py`) e botões Import/Export JSON no
  front (aba Build). Mesmo schema `{query, deck}` nos dois lados -- foi o que permitiu
  salvar `eval/scenarios/aminatou_esper_enchantments_full_deck.json` direto de um export
  real em vez de copiar o JSON à mão.
- Revisão pré-commit (`/pre-commit`) rodada duas vezes sobre o diff da sessão; achados
  corrigidos: docstring do `eval_retrieval.py` que citava esse próprio arquivo (gitignored,
  quebraria pra quem clonar o repo), comandante inválido em `--import` seguindo silencioso
  em vez de erro, duplicidade na aplicação de `owned_only`, e `json.load(--import)` sem
  tratamento de exceção. Ainda não commitado -- split sugerido em 3 commits (fix do
  `cut()` / import-export / harness de eval), aguardando o usuário rodar.

### Próximos passos sugeridos

1. Desenhar e implementar o benchmark de seleção/scoring (Camada 2, metade que falta),
   usando o fixture que já está salvo.
2. Só depois de ter os dois benchmarks (retrieval + seleção) rodando, decidir onde mexer
   pra melhorar os números -- `ROLE_QUERIES` mais específicas por arquétipo, dar mais peso
   à query do usuário/oracle_text do commander, etc. -- e comparar antes/depois nos dois.
3. Adicionar mais arquétipos ao benchmark de retrieval (hoje só tem Aminatou/Esper
   enchantments).

## Sessão 2026-08-21 (parte 2) — detecção de tema no retrieval

Continuação direta da Camada 2. Objetivo: dar ao `candidate_pool` alguma noção de
arquétipo além do texto cru do usuário + `ROLE_QUERIES` genéricas (o próprio Problema B).

### O que foi implementado e ficou

- `src/solver.py`: `detect_known_themes(cmd)` -- keyword-only, mesmo padrão de
  `self_referential_types` (o gate tribal já existente): se a palavra literal
  "enchantment"/"artifact"/"graveyard" aparece no oracle_text do comandante, dispara
  as queries daquele tema em `THEME_QUERIES`. Risco aceito e documentado no código: sem
  checagem de polaridade -- "destroy target enchantment" dispara o tema igual a "draw a
  card whenever an enchantment enters".
- `THEME_QUERIES["enchantment"]` ficou com **1 query só**, não 4. As outras 3 (bare
  "enchantment", constellation, eerie) foram testadas e cortadas -- ver metodologia
  abaixo. `artifact` e `graveyard` mantêm 2 queries cada, mas **nunca foram
  auditadas** por falta de benchmark que exercite esses temas -- tratar como não
  comprovadas até existir um arquétipo de artefato/cemitério no benchmark.
- Cap de queries em `_retrieve()` subiu de `queries[:8]` pra `queries[:16]` pra acomodar
  o pior caso (2 base + 1 tribal + até 5 de tema + 5 `ROLE_QUERIES` = 13, com folga).

### O que foi tentado e revertido: filtro por metadado de tipo no Chroma

Testado: `is_enchantment`/`is_artifact` como bits de metadado no Chroma (mesmo padrão
dos bits de cor já existentes), usados como um canal de busca extra filtrado por
`where` em vez de só semântico. Hipótese: resolveria o caso de cartas cujo type_line é
"Enchantment" mas o oracle_text é longo/não-relacionado (o embedding dilui o sinal de
tipo). Medido contra `aminatou_esper_enchantments`: adicionou 31 candidatos ao pool
(270→~300 nesse teste isolado), **zero** deles na lista boa ou ruim -- inchaço sem
efeito em recall. Revertido por completo (`geometry.py`/`hybrid_search.py` voltaram
byte-a-byte ao estado do commit anterior). Fica registrado aqui, não no código, porque
é histórico de experimento, não comportamento do sistema.

### Metodologia nova: atribuição de hit por query

Em vez de medir só "essa query traz resultado que nenhuma outra traz" (unicidade
estrutural), passou a medir "essa query traz uma carta que está na lista boa do
benchmark" (unicidade que importa). Rodando isso nas 4 queries de encantamento: só a
frase de draw (`"whenever you cast an enchantment or an enchantment enters the
battlefield, draw a card"`) trouxe `Mesa Enchantress` e `Monastery Siege`; as outras 3
não trouxeram nenhum hit da lista boa. Daí o corte pra 1 query.
Ressalva: o benchmark só tem 1 carta que poderia validar a query de "eerie" (`Entity
Tracker`, que mesmo assim não aparece -- ver achado abaixo) e nenhuma pra "constellation"
-- não é prova de que esses ângulos são inúteis em geral, só que esse único arquétipo
não consegue confirmar nem negar. Reavaliar se/quando existir um arquétipo que os
exercite de verdade.

### Correção de 2 rótulos do benchmark

- `Dawn of a New Age` (good) trocado por `Phyrexian Arena`: o primeiro custava $10.14,
  acima do `max_card_price: 5` do cenário -- nunca poderia aparecer no pool, o teste
  media price cap, não retrieval. `Phyrexian Arena` ($3.88, identidade B) é um draw
  engine clássico e cabe no cap.
- `Enduring Innocence` (good) trocado por `Ashiok's Reaper`: o oracle_text real de
  `Enduring Innocence` é "Whenever one or more **other creatures** you control with
  power 2 or less enter, draw a card" -- o gatilho é sobre criatura pequena entrando,
  não sobre encantamento. Só é "Enchantment Creature" no type_line; nunca deveria ter
  sido rotulada como alvo de retrieval por tema de encantamento. `Ashiok's Reaper`
  ($0.22, B) é um payoff real: "Whenever an enchantment you control is put into a
  graveyard from the battlefield, draw a card."

### Novo baseline (após tema + poda de queries + rótulos corrigidos)

```
candidate_pool size: 382   (baseline original: 270)
good recall: 3/10 (30%)    (baseline original: 1/10, 10%)
bad rate:    9/10 (90%)    (sem mudança)
```

Ainda faltam (todos genuínos, nenhum é bug de benchmark):
- `Arcane Denial`, `Muddle the Mixture`, `Dovin's Veto`, `Negate` -- 4 counterspells,
  preço e cor OK, sem relação com o tema de encantamento. Gap de retrieval de
  counterspell, não investigado ainda.
- `Entity Tracker` -- ability word "eerie" (Duskmourn) parece competir com o sentido
  comum da palavra em inglês (assombroso/estranho) no embedding. Mesmo uma query quase
  verbatim do próprio oracle_text da carta a rankeia em #1064 de 5000, sem filtro
  nenhum. Sem solução proposta ainda.
- `Ashiok's Reaper` -- payoff real de encantamento indo pro cemitério, ainda não
  encontrado por nenhuma query atual.

### Achado novo e mais preocupante: `Phyrexian Arena` não bate nem em "draw a card"

Fora do tema de encantamento: `Phyrexian Arena` (staple de draw repetível, oracle_text
"At the beginning of your upkeep, **you draw a card** and you lose 1 life") rankeia
**#1527 de 5000** pra query genérica `"draw a card"` -- uma das 5 `ROLE_QUERIES` que
roda sempre, sem filtro de cor/preço/tema envolvido. O texto tem "draw a card" quase
literal e mesmo assim o embedding não aproxima. Suspeita: "you lose 1 life" puxa o
vetor da frase pra um sentido diferente (perda de vida/risco), competindo com o "draw"
na mesma sentença embedada. Isso não é um problema de tema -- é uma dúvida sobre o
`ROLE_QUERIES` genérico em si, que nunca foi auditado da mesma forma que o tema de
encantamento acabou de ser. Vale abrir como investigação própria.

### Próximos passos sugeridos (atualizado)

1. Investigar o gap de counterspell (4 cartas boas nunca aparecem) -- separado do
   trabalho de tema, pode ser problema nas `ROLE_QUERIES` ou falta de uma delas
   específica pra counterspell.
2. ~~Investigar o achado do `Phyrexian Arena`/`"draw a card"`~~ -- feito, ver sessão
   abaixo. Não é caso isolado: é sistêmico em toda carta de draw-engine repetível.
3. Se/quando existir um arquétipo de artefato ou cemitério no benchmark, auditar
   `THEME_QUERIES["artifact"]`/`["graveyard"]` do mesmo jeito -- hoje são só palpite.
4. Segue de pé: benchmark de seleção/scoring (item 1 da lista anterior) e mais
   arquétipos no benchmark de retrieval.

## Sessão 2026-08-21 (parte 3) — causa raiz do gap de `Phyrexian Arena`/`"draw a card"`

Investigação isolada, sem LLM: consulta direta ao Chroma local (`data/chroma_db`,
collection `oracle_cards`, 38619 cartas, MiniLM `all-MiniLM-L6-v2`) e cálculo manual de
cosine sim sobre o mesmo texto de documento que `vectorize_cards._document()` gera em
produção (`"{name} - {type_line}. Effect: {oracle_text}"`). Script descartável, não
commitado (rodado do scratchpad da sessão).

### Reprodução confirmada

`Phyrexian Arena` pra query `"draw a card"`: rank **#1527 de 5000**, distance 0.6012 --
bate exatamente com o número já registrado na parte 2. Confirmado que a distância do
Chroma aqui é `1 - cosine` (checado: cosine manual 0.3988 == 1 - 0.6012).

### Não é caso isolado -- é sistêmico em toda carta de draw-engine repetível

Mesma query `"draw a card"`, outras cartas clássicas de draw engine (gatilho ou custo,
não cantrip único):

| Carta | Rank (de 5000) |
|---|---|
| Sylvan Library | #657 |
| Sign in Blood | #708 |
| Phyrexian Arena | #1527 |
| Necropotence | #2573 |
| Dark Confidant | #3732 |
| Bloodgift Demon | fora do top 5000 |

Pro contraste, o top 10 real da mesma query é só cantrip/spell de draw único e frase
curta: `Mental Note`, `Quick Study`, `Tidings`, `Library of Alexandria`, etc. -- textos
onde "draw" é a fração dominante do documento inteiro.

### Causa raiz isolada: o template do documento dilui mais que o texto da carta

Decompondo a cosine sim contra `"draw a card"` em camadas, saindo do oracle_text puro até
o documento real de produção:

| Texto embedado | cosine vs `"draw a card"` |
|---|---|
| `"draw a card"` (a própria query) | 1.0000 |
| oracle_text sozinho, sem "lose 1 life", sem prefixo | 0.7037 |
| oracle_text completo (com "lose 1 life"), sem prefixo | 0.5426 |
| `"{name} - {type}. Effect: {oracle}"`, sem "lose 1 life" | 0.4635 |
| `"{name} - {type}. Effect: {oracle}"` completo (doc real de produção) | 0.3988 |

Dois efeitos aditivos, de magnitude bem diferente:
- **O prefixo `"{name} - {type_line}. Effect: "` sozinho custa ~0.24 de cosine**
  (0.70 → 0.46) -- maior efeito de longe. Todo documento no índice paga esse custo, não
  só cartas de draw.
- **A cláusula extra "and you lose 1 life" custa outros ~0.06** (0.54 → 0.48, ou 0.46 →
  0.40 já com prefixo) -- real, mas secundário.

Ou seja: o problema não é "life loss confunde o embedding" (a hipótese original da parte
2) -- é mais estrutural. MiniLM (`all-MiniLM-L6-v2`, modelo pequeno, mean-pooling) com uma
query curta de 3 palavras favorece fortemente documentos onde a query é quase o texto
inteiro. Qualquer coisa que alongue o documento -- o prefixo fixo de nome/tipo, uma
condição de gatilho ("at the beginning of your upkeep"), um custo -- dilui a proporção do
conceito-alvo no vetor e derruba a similaridade, mesmo com o texto batendo quase
literalmente. Teste de confirmação (parte 2, já registrado): a mesma carta sobe pra
**rank #55** só trocando a query genérica de 3 palavras por uma frase que reproduz a
estrutura completa do oracle_text (`"at the beginning of your upkeep, you draw a
card"`) -- ou seja, o modelo consegue achar a carta, só não com uma query curta e
genérica.

### Consequência pro sistema

`ROLE_QUERIES` genéricas de poucas palavras (`"draw a card"`, e provavelmente as outras
4 da lista -- não testadas ainda) sistematicamente sub-representam cartas de engine
(gatilho recorrente ou custo) no `candidate_pool`, favorecendo cantrips/spells de efeito
único. Isso é uma fonte de viés de retrieval independente do Problema B (tema) já
identificado -- afeta *todo* arquétipo, não só o de encantamento, porque `ROLE_QUERIES`
roda sempre.

### Próximos passos sugeridos (novo)

1. Repetir a mesma decomposição pras outras 4 `ROLE_QUERIES` (ramp, removal, tokens,
   basic land) -- confirmar se o mesmo viés de "query curta perde pra doc longo" aparece
   em todas ou só em draw.
2. Opções de mitigação a avaliar (nenhuma implementada ainda):
   - Queries mais longas/naturais no lugar das curtas (o teste de "at the beginning of
     your upkeep, you draw a card" sugere que isso ajuda bastante, mas não generaliza --
     precisaria ser uma frase por conceito, não uma palavra-chave).
   - Reduzir o prefixo do documento (`"{name} - {type}. Effect: "`) ou mover
     nome/type_line pra metadata em vez de concatenar no texto embedado -- maior
     alavanca isolada (~0.24 de cosine), mas é uma reindexação completa (38k cartas,
     custo de recompute) e pode afetar todo o resto do sistema que depende desse
     formato de documento, não só draw.
   - Múltiplas queries curtas por conceito (já existe pra tema) especificamente pra
     "draw engine" vs "draw cantrip" como conceitos separados, em vez de tentar consertar
     a query genérica única.
3. Decidir se vale medir isso formalmente no benchmark (Camada 2) como uma categoria
   própria ("draw engines" como sub-lista dentro da lista boa) em vez de só descoberta
   ad-hoc.

## Sessão 2026-08-21 (parte 4) -- alternativas pra diferenciar cartas-de-draw de engines-de-draw

Continuação direta da parte 3: já que a causa raiz é estrutural no embedding (query
curta perde pra doc longo), a pergunta virou "dá pra resolver sem mexer no
índice/modelo?". Testadas duas alternativas com evidência; scripts descartáveis, não
commitados.

### Alternativa A -- classificador determinístico por regex sobre oracle_text (sem embedding)

Mesmo padrão de `roles.py::is_card_advantage_draw` (que já existe e já é usado em
`classify_roles`). Ideia: `is_draw_engine(oracle_text)` = tem sinal de card-advantage
(`draws?` ou `into (your|their) hand`, pra cobrir cartas tipo Dark Confidant que usam
"put into your hand" em vez da palavra "draw") **e** tem marcador de recorrência
(`whenever`, `at the beginning of`, `cumulative upkeep`, `each turn/upkeep/draw step`).

Validado em 2 etapas:
1. **Conjunto curado (19 cartas conhecidas, 9 engines / 10 cantrips)**: 100% de acurácia
   depois de ajustar o gate pra incluir "into your hand" (sem o ajuste, `Dark Confidant`
   dava falso negativo -- não usa a palavra "draw"). Achado colateral: `Fact or Fiction`
   nem o `is_card_advantage_draw` original do `roles.py` reconhece como draw (nunca usa
   "draw" nem "into hand" -- usa "put one pile into your hand", que passou no meu regex
   mas não no gate mais estrito de `roles.py`. Não é bug, é um limite conhecido do
   `roles.py` que já existia antes desta sessão.)
2. **Amostra aleatória de 20 cartas reais** (de 2256 cartas do catálogo inteiro
   classificadas como "engine" pelo regex, dentro de 6105 que batem no gate de
   card-advantage, `seed=42`): a acurácia caiu pra ~75% (5 falsos positivos claros em
   20). Confirma que o conjunto curado era fácil demais -- a validação em escala real é o
   que importa. Falsos positivos encontrados, com carta e motivo:
   - **`Gavi, Nest Warden`**: "whenever you draw your second card each turn" -- "draw"
     aparece como *condição de gatilho de outra coisa* (cria token), não como efeito
     concedido pela própria carta. Gavi não dá card advantage nenhum.
   - **`Jacob Frye`**: "target player may put Evie into their hand" é texto de regra fixo
     do mecanismo Partner-with, presente em toda carta com Partner -- não tem nada a ver
     com a carta ser ou não uma engine de draw. Falso positivo sistemático, não
     eventual: qualquer carta com Partner-with mais um "whenever" de outra habilidade
     cai nessa armadilha.
   - **`Vexing Sphinx`**: "Cumulative upkeep" dispara o marcador de recorrência, mas o
     draw da carta é um gatilho de morte de uso único ("When this creature dies, draw"
     -- `when`, não `whenever`). O regex não liga qual cláusula de recorrência está de
     fato conectada ao draw.
   - **`Molten Firebird`**: "you skip your next draw step" -- "draw" aparece como
     substantivo de fase de jogo ("draw step"), não como o verbo "draw a card". A carta
     não dá nenhuma carta, ao contrário, pula uma compra.
   - **`Kiora, the Rising Tide`**: o draw é um ETB de uso único ("When Kiora enters, draw
     two cards"); o marcador de recorrência veio de uma cláusula completamente
     desconectada mais adiante no texto ("Whenever Kiora attacks..." -- cria um token,
     não dá carta). Exatamente o modo de falha "cláusula não relacionada" hipotetizado
     antes de rodar o teste.
   Em comum: o regex não verifica *proximidade/ligação causal* entre o marcador de
   recorrência e o efeito de draw -- só "os dois padrões aparecem em algum lugar do
   texto". Pra um filtro duro (excluir carta do pool) isso é arriscado; pra um bônus de
   score leve (como os outros bônus em `roles.py`/`solver.py`) é mais tolerável, já que
   o dano de um falso positivo é só "uma carta a mais concorrendo", não uma exclusão.

### Alternativa B -- query de retrieval dedicada a "engine" (embedding, mesma linha da parte 3)

Testado com o corte real de produção (`n_results=160` por query, não os 5000 usados só
pra diagnóstico na parte 3) contra as 9 cartas-engine de referência
(`Phyrexian Arena`, `Sylvan Library`, `Necropotence`, `Dark Confidant`, `Bloodgift Demon`,
`Rhystic Study`, `Mystic Remora`, `Consecrated Sphinx`, `Coastal Piracy`):

- Baseline (`"draw a card"`, a `ROLE_QUERY` atual): **0/9** entram no top 160. Confirma
  que na prática, hoje, nenhuma dessas 9 cartas chega no `candidate_pool` via essa query
  -- não é só "rank ruim dentro de 5000", é exclusão completa do corte real.
- Melhor query única testada (frase quase verbatim do oracle_text de
  `Phyrexian Arena`): **2/9** (`Phyrexian Arena`, `Mystic Remora`).
- **União de 5 queries, uma por formato de gatilho** (upkeep, "opponent casts",
  combat damage, morte de criatura, draw step) -- simula como a produção já funciona
  hoje (múltiplas queries, resultados unidos no mesmo `candidate_pool`, mesmo padrão do
  `THEME_QUERIES`): **5/9** (`Phyrexian Arena`, `Mystic Remora`, `Rhystic Study`,
  `Coastal Piracy`, `Sylvan Library`). Ficaram de fora `Bloodgift Demon`,
  `Consecrated Sphinx`, `Dark Confidant`, `Necropotence` -- cada um com um verbo/estrutura
  de gatilho ligeiramente diferente da query escrita à mão (`Bloodgift Demon` usa
  "target player draws", não "you draw"; `Necropotence` não usa a palavra "draw" em
  lugar nenhum). Pra cobrir as 9 seria necessário escrever uma query por variação de
  fraseado -- não generaliza, é o mesmo custo de curadoria manual do `THEME_QUERIES`
  mas por card individual em vez de por arquétipo.

### Comparação e recomendação

| | Alternativa A (regex) | Alternativa B (query dedicada) |
|---|---|---|
| Custo de implementação | Baixo -- função pura, mesmo padrão já existente em `roles.py` | Alto -- uma query por formato de gatilho, curadoria manual por carta |
| Recall nas 9 engines de referência | 9/9 (no conjunto curado); não medido diretamente sobre o pool real, mas não depende de embedding | 5/9 mesmo com 5 queries escritas à mão |
| Falso positivo | ~25% numa amostra aleatória de 20 (5 casos, todos com causa identificada) | N/A (retrieval não "classifica", só ordena por distância) |
| Generaliza pra cartas fora do padrão de frase testado? | Sim -- é sobre estrutura da oracle_text (whenever/at the beginning of), não sobre frase específica | Não -- cada nova "forma" de engine (novo verbo de gatilho) exige nova query |
| Onde plugaria no sistema | Bônus de score em `fill()`/`cut()` (linha de `role_need_bonus`/`_token_adj`), não como filtro duro de retrieval | Mais uma entrada em `THEME_QUERIES`-like ou `ROLE_QUERIES` |

Recomendação com base no que foi medido: **Alternativa A é a mais forte**, mas não como
filtro que decide o que entra no `candidate_pool` (os falsos positivos documentados
harmoniam mal com uma exclusão dura) -- e sim como um sinal extra de scoring dentro de
`fill()`, do mesmo jeito que `role_need_bonus`/`_tribe_adj` já funcionam: soma um bônus
quando o deck ainda precisa de "draw" (`ROLE_QUOTAS["draw"]`) e a carta candidata bate
`is_draw_engine`. Isso não resolve o problema de retrieval em si (a carta ainda precisa
entrar no `candidate_pool` primeiro, por alguma query) -- só resolve a metade de
"dado que a carta está no pool, o scoring reconhece que é uma engine e não só um
cantrip". A Alternativa B, sozinha, não compensa o custo de curadoria por card.

### O que ficou faltando (não é bug, é trabalho ainda não feito)

- Nenhuma das duas alternativas foi implementada em `solver.py`/`roles.py` -- só
  avaliadas com scripts descartáveis fora do repo.
- Não testado: um terceiro canal de retrieval que pule o Chroma inteiramente pra esse
  caso -- varrer o catálogo (SQLite) direto com `is_draw_engine` + filtros de cor/preço já
  existentes, sem depender de distância de embedding nenhuma. Rodar o regex nas 35663
  cartas do catálogo já levou frações de segundo neste experimento (sem custo de
  embedding envolvido), então o custo não é o problema -- é uma decisão de arquitetura
  (um segundo canal de candidate_pool, determinístico, paralelo ao semântico) que não foi
  desenhada nem testada ainda. Combinaria o recall de A com uma garantia de inclusão que
  nem A (como bônus de score) nem B (como retrieval) dão sozinhas.
- Falso positivo de A não foi medido em cima do gate de precisão/proximidade (ex: exigir
  que o marcador de recorrência apareça na mesma sentença que o marcador de draw) --
  seria a correção óbvia pros 5 casos encontrados (`Gavi`, `Jacob Frye`, `Vexing Sphinx`,
  `Molten Firebird`, `Kiora`), mas não foi tentada nem medida.

## Sessão 2026-08-21 (parte 4.5) -- correção de rumo: bônus de score é a alavanca errada

A alternativa A foi implementada como bônus de score (`ENGINE_BONUS = 0.6` em
`_score_parts`, gate em `role_bonuses.get("draw") > 0`, `is_draw_engine` movido pra
`roles.py`) e depois **revertida a pedido do usuário** (`git diff` limpo, 68/68 testes OK
depois do revert). Motivo, correto e confirmado com evidência: bônus de score só afeta
`fill()`/`cut()`/`score_candidate()`, que só rankeiam entre as cartas que **já estão** no
`candidate_pool`. `eval_retrieval.py` roda só `_retrieve()` -- não passa por scoring
nenhum -- então o benchmark não podia mesmo mudar de número com essa alteração. Rodado
mesmo assim como prova: `candidate_pool size: 382` idêntico ao baseline, `Phyrexian
Arena` continua em `good_missed`. Confirma o que já estava documentado nas partes 3/4: o
problema é a carta nem entrar no pool, não o pool ordenar mal.

Fica registrado o erro de sequenciamento: a alternativa A deveria ter sido testada contra
"a carta entra no pool?" antes de implementar "a carta pontua melhor dado que já está no
pool?" -- as duas perguntas são independentes e a segunda é inútil sem a primeira.

Observação do usuário que fica valendo como princípio de design daqui pra frente: a
pergunta "engine vs cantrip" só importa porque a query deste arquétipo pede
explicitamente por "engines de draw". Se o usuário não tivesse pedido isso, cantrips
serem favorecidos no candidate_pool não seria um problema grande -- ou seja, a correção
não deveria virar uma regra geral de "engine sempre vale mais que cantrip", e sim algo
que só entra em jogo quando o pedido específico justifica.

## Sessão 2026-08-21 (parte 5) -- testando um canal de retrieval determinístico, paralelo ao Chroma

Direto na sugestão que ficou em aberto na parte 4: pular o Chroma inteiramente pro
conceito de "draw engine" e injetar candidatos no pool via um scan determinístico do
catálogo (SQLite + `is_draw_engine` + os mesmos filtros de cor/preço que `_retrieve()` já
aplica). Testado com script descartável, nada implementado em `solver.py`.

### O canal encontra as cartas certas, mas em volume grande demais

Filtro: `is_draw_engine(oracle_text)` and colors ⊆ {W,U,B} and price <= 5 and não-land,
nos 35663 cards do catálogo (scan completo em 162ms, sem custo de embedding).

```
Candidatos: 1244 (de 35663)
Phyrexian Arena: IN
Ashiok's Reaper: IN
```

Prova que o canal *encontra* as duas cartas de referência que a busca semântica nunca
encontrou (parte 3/4). Mas 1244 candidatos de um único conceito é ~3-4x o
`candidate_pool` inteiro de hoje (382, somando todas as ~13 queries de tema/role juntas)
-- grande demais pra injetar sem nenhum corte.

### Tentativa de cortar por embedding (rank-dentro-do-subconjunto): não funciona

Hipótese testada: já que o problema da parte 3 era a query curta perder pra doc longo
*na escala do catálogo inteiro* (35663 documentos concorrendo), talvez dentro de um
subconjunto pequeno e já estruturalmente filtrado (1244, todos comprovadamente engines de
draw) a distância de embedding voltasse a ser um sinal útil pra ordenar por relevância.
Testado de duas formas, ambas usando as 1244 cartas do canal determinístico como universo:

1. **Embeddings de produção do Chroma** (documento completo, já armazenado, sem
   recomputar): `Phyrexian Arena` rank **#144 de 1244** pra `"draw a card"` -- fora de um
   corte de top-40 mesmo dentro do subconjunto pré-filtrado.
2. **Embedding só do oracle_text, sem o prefixo `"{name} - {type}. Effect:"`** (o maior
   fator de diluição identificado na parte 3, ~0.24 de cosine): `Phyrexian Arena` sobe pra
   rank **#58 de 1244** -- melhora, mas ainda fora do top-40.

Pior: os dois sinais **discordam entre si** (`Ashiok's Reaper` rank #513 pelo doc
completo vs #351 pelo oracle-only, mesma query) -- não é uma melhoria consistente, é
ruído. E rodando a query real do usuário em português (`"Quero 5 engines de draw e 5
counterspells..."`) em vez do `"draw a card"` genérico em inglês, o resultado piora
drasticamente: `Phyrexian Arena` cai pra rank **#1050 de 1244** no embedding oracle-only
-- suspeita de mismatch cross-lingual do MiniLM (modelo treinado majoritariamente em
inglês, query em português não alinha bem com oracle_text em inglês). Achado novo, não
investigado antes: a língua da query do usuário pode ser, sozinha, um fator de degradação
tão grande quanto os já documentados (prefixo do doc, diluição por frase longa).

**Conclusão da tentativa**: nenhuma variante de embedding testada é um sinal confiável
pra cortar o subconjunto de 1244 até um tamanho injetável. Rankear por distância dentro
de um conjunto já estruturalmente filtrado não recupera o que a busca semântica perdeu --
é a mesma fraqueza de fundo (MiniLM + query curta) reaparecendo em escala menor, agora
com o agravante do mismatch de idioma.

### Custo de performance de simplesmente não cortar

`fill()` (linha ~217 de `solver.py`) itera a lista inteira de `candidates` chamando
`score_candidate` a cada card, a cada iteração do loop guloso (um card adicionado por
vez, até `remaining_slots()` -- dezenas de iterações num deck de 99 cartas). Hoje isso já
é O(candidatos × slots) com ~270-382 candidatos. Injetar 1244 candidatos extra sem corte
nenhum multiplicaria esse custo por ~3-4x -- não é só "mais sujeira no pool", é impacto
real de performance no `fill()`, não medido diretamente ainda (não cheguei a rodar um
`fill()` de ponta a ponta com o pool inflado).

### Estado em aberto -- decisão de arquitetura, não resolvida

O canal determinístico prova que **existe** um caminho pra Phyrexian Arena entrar no
pool (nenhuma variante de busca semântica testada até agora consegue isso). O que falta
decidir, sem experimento ainda rodado pra nenhuma das opções:
- Cortar por um critério **não-embedding**: preço, CMC, ou simplesmente um cap fixo (top
  N por ordem alfabética/aleatória) e deixar o `fill()`/`cut()` (que já tem sinais mais
  ricos que cosine puro -- synergy, role bonuses, redundância) fazer a seleção de
  qualidade de fato.
- Não cortar, aceitar o pool maior, e medir se o custo de performance do `fill()` é
  realmente proibitivo ou só teoricamente maior (não medido).
- Amarrar a ativação desse canal à necessidade de "draw" por `ROLE_QUOTAS` (só liga
  quando o deck ainda precisa de draw) em vez de sempre rodar -- reduziria a frequência
  do custo, não o tamanho do pool quando ativo.
- Testar se o problema de mismatch de idioma (achado novo desta sessão) também afeta a
  busca semântica normal hoje -- se a maioria das queries reais dos usuários vem em
  português, isso pode ser um fator sistemático nunca antes isolado, maior até que os já
  documentados.

## Sessão 2026-08-21 (parte 6) -- queries que resolvem os 3 casos, dentro do top 40 real

Objetivo: achar queries que tragam os resultados esperados dentro do top-40 real (mesmos
parâmetros de produção: WUB, price<=5, `limit=40`, `n_results=160`, via
`search_cards()`, não Chroma cru). Query do arquétipo trocada pra inglês
(`eval/archetypes/aminatou_esper_enchantments.json`) a pedido do usuário -- elimina o
mismatch cross-lingual como variável de confusão nos testes.

### Correção de metodologia: 4 dos 8 alvos de "engine geral" nunca poderiam aparecer

Antes de testar queries, os 8 alvos de engine-geral usados nas partes 3/4
(`Phyrexian Arena`, `Sylvan Library`, `Necropotence`, `Dark Confidant`, `Bloodgift Demon`,
`Rhystic Study`, `Mystic Remora`, `Consecrated Sphinx`, `Coastal Piracy`) nunca tinham
sido checados contra o price cap de $5 do cenário. Checados agora:

| Carta | Preço | Passa o cap de $5? |
|---|---|---|
| Necropotence | $37.37 | Não |
| Rhystic Study | $70.10 | Não |
| Mystic Remora | $13.04 | Não |
| Consecrated Sphinx | $31.00 | Não |
| Phyrexian Arena | $3.88 | Sim |
| Dark Confidant | $3.25 | Sim |
| Bloodgift Demon | $0.86 | Sim |
| Coastal Piracy | $0.29 | Sim |

4 das 8 nunca poderiam entrar no `candidate_pool` desse cenário **de jeito nenhum**,
qualquer que fosse a query -- o filtro de preço as exclui antes mesmo do ranking. Isso não
é falha de retrieval, é o filtro de preço funcionando corretamente. Corrige uma
premissa implícita das partes 3/4 (a lista de 8 nunca tinha sido validada contra os
filtros reais do cenário). O conjunto de alvos realmente alcançável nesse cenário ficou
menor: 4 pra engine-geral, e (verificado do mesmo jeito) 7 de 8 pra draw-geral
(`Night's Whisper` também ficou de fora por preço: $5.37, passa 37 centavos do cap).

### Metodologia

Testado com `RAGSearcher.search_cards()` direto (o mesmo caminho de `_retrieve()`),
iterando frase por frase e medindo o rank de cada carta-alvo dentro do top 40 real.
Confirma o padrão já visto nas partes 3/4: frase quase-verbatim do oracle_text da própria
carta funciona; paráfrase genérica ("recurring card advantage engine...", "card draw
payoff engine...") não traz nenhum hit -- testado e descartado.

### Caso 1a -- "cartas que compram" (draw geral, sem exigir recorrência)

5 queries, 7/7 alvos alcançáveis cobertos:

| Query | Cobre |
|---|---|
| `"draw a card"` | Divination #10, Tidings #5, Brainstorm #38, Stroke of Genius #29 |
| `"draw cards"` | Divination #7, Tidings #2, Brainstorm #15, Stroke of Genius #16 |
| `"Draw three cards."` | Concentrate #34, Brainstorm #3 |
| `"Target player draws two cards and loses 2 life."` | Sign in Blood #3 |
| `"Reveal the top five cards of your library. An opponent separates those cards into two piles. Put one pile into your hand and the other into your graveyard."` | Fact or Fiction #1 |

`"draw cards"` (plural, sem artigo) rankeia consistentemente melhor que `"draw a card"`
pras mesmas cartas -- ganho pequeno mas real e de graça (mesmo texto, sem custo extra).
`Fact or Fiction` só aparece com uma query que reproduz sua mecânica real (revelar +
dividir em pilhas) -- não tem a palavra "draw" no oracle_text, então nenhuma query
genérica de draw jamais a encontraria.

### Caso 1b -- "engines de draw" gerais (sem tema)

4 queries, 4/4 alvos alcançáveis cobertos -- essencialmente uma query por carta,
usando o oracle_text quase literal:

| Query | Acha |
|---|---|
| `"at the beginning of your upkeep, you draw a card and you lose 1 life"` | Phyrexian Arena #4 |
| `"At the beginning of your upkeep, reveal the top card of your library and put that card into your hand. You lose life equal to its mana value."` | Dark Confidant #26 |
| `"Flying\nAt the beginning of your upkeep, target player draws a card and loses 1 life."` | Bloodgift Demon #3 |
| `"whenever a creature you control deals combat damage to an opponent, you may draw a card"` | Coastal Piracy #8 |

Confirma o padrão da parte 4 (união de 5 queries por formato de gatilho, 5/9): não existe
generalização por "conceito de engine" -- cada carta precisa da sua frase, próxima o
bastante do próprio oracle_text. Achado curioso: pra `Bloodgift Demon`, incluir a keyword
`"Flying"` (irrelevante pro conceito de draw) antes do texto de upkeep muda o resultado de
"não encontrado" pra rank #3 -- reforça que o mecanismo é overlap textual bruto com o
documento de produção, não compreensão do conceito.

### Caso 2 -- "engines de draw com sinergia de encantamento"

2 queries, 2/2 alvos cobertos, ambas em posição de destaque:

| Query | Acha |
|---|---|
| `"whenever you cast an enchantment spell, you may draw a card"` | Mesa Enchantress **#1** |
| `"whenever an enchantment you control is put into a graveyard, draw a card"` | Ashiok's Reaper **#2** |

O caso mais fácil dos três -- só 2 alvos conhecidos no benchmark, e ambos batem quase
verbatim com o oracle_text real de cada carta (`Mesa Enchantress`: "Whenever you cast an
enchantment spell, you may draw a card."; `Ashiok's Reaper`: "Whenever an enchantment you
control is put into a graveyard from the battlefield, draw a card.").

### O que isso prova, e o que não prova

**Prova**: pra cada um dos 3 casos, existe uma combinação pequena de queries (2 a 5) que
traz os alvos conhecidos pro top-40 real deste cenário específico (Aminatou, WUB,
price<=5). Nenhuma delas foi adicionada a `ROLE_QUERIES`/`THEME_QUERIES` em `solver.py` --
só validadas isoladamente com `search_cards()`.

**Não prova**: que essas queries generalizam pra qualquer arquétipo, qualquer price cap,
ou qualquer carta de draw engine fora dessas 4+2+7 especificamente testadas. O padrão que
se repete em todas as partes (3, 4, 6) é o mesmo: quanto mais próxima a query fica do
oracle_text literal da carta-alvo, melhor o rank -- e isso, por construção, não
generaliza pra cartas com fraseado diferente. Continua valendo a ressalva da parte 4: uma
query por carta/gatilho não escala pra manter conforme o catálogo cresce ou pra novos
arquétipos.

### Próximos passos sugeridos

1. Decidir se as 11 queries encontradas (5+4+2) valem a pena virar `THEME_QUERIES`/
   `ROLE_QUERIES` permanentes em `solver.py` mesmo sabendo que são curva-ajustadas a
   cartas específicas -- ou se o retorno (7+4+2 = 13 cartas garantidas) já justifica o
   custo de manutenção.
2. Se formalizado, decidir o gate de ativação: sempre rodar (como `ROLE_QUERIES` hoje) ou
   só quando o `ROLE_QUOTAS["draw"]` ainda não foi atingido (evita gastar cota de query
   quando o deck já tem draw suficiente).
3. Testar o mesmo processo em outro arquétipo/price cap pra ver se as queries encontradas
   aqui generalizam melhor do que o esperado, ou se cada cenário realmente precisa do seu
   próprio conjunto curado.

## Sessão 2026-08-21 (parte 7) -- "draw a card" vs "draw cards", e se dá pra generalizar a query de engine

Dois testes pontuais pedidos pelo usuário, pra decidir se vale manter as duas variantes
singular/plural e se uma frase mais genérica consegue substituir as queries
per-carta da parte 6. Query do Fact-or-Fiction (a de "reveal the top five cards...")
descartada por pedido explícito -- forçação desnecessária pra achar uma única carta.

### Teste A -- `"draw a card"` vs `"draw cards"` contra a lista completa good/bad do benchmark

Nenhuma das duas encontra **nenhuma** das 11 cartas "good" do arquétipo (o good list é
counterspells + engines temáticas -- nenhuma é um cantrip simples de "draw N cards").
`"draw a card"` ainda traz 1 falso positivo (`Research Assistant`, que está na lista
"bad", rank #13); `"draw cards"` não traz nenhum hit bad. Das 40 vagas de cada query,
31 cartas são compartilhadas e 9 são exclusivas de cada lado -- não são a mesma lista,
mas o grosso se sobrepõe.

Juntando com o que já estava medido na parte 6 (`"draw cards"` rankeia as mesmas cartas
melhor que `"draw a card"` -- Tidings #5→#2, Divination #10→#7, Brainstorm #38→#15,
Stroke of Genius #29→#16, sem nenhum custo): **não há razão pra manter as duas**. Pra esse
benchmark específico as duas são igualmente inúteis (zero hits good); pro conceito geral
de "draw" (parte 6, caso 1a), plural rankeia igual ou melhor de graça. Recomendação:
manter só `"draw cards"`, descartar `"draw a card"`.

### Teste B -- existe uma frase genérica que substitua as queries per-carta da parte 6?

Hipótese testada: uma frase mais natural/genérica de "no início do turno você compra
cartas" (em vez do oracle_text quase-literal de uma carta específica) conseguiria
generalizar o suficiente pra pegar `Phyrexian Arena` **e** `Monastery Siege` (e talvez
outras) na mesma query, evitando o problema de "uma query por carta" documentado na
parte 6.

5 fraseados testados, todos genéricos o bastante pra não citar nenhuma carta específica:
`"at the beginning of your turn you draw cards"`, `"at the beginning of your upkeep you
draw cards"`, `"at the beginning of your upkeep, draw a card"`, `"each turn you draw an
extra card"`, `"at the beginning of your draw step, draw an additional card"`.

**Resultado: 0 de 5 encontrou qualquer um dos 5 alvos testados** (`Phyrexian Arena`,
`Monastery Siege`, `Dark Confidant`, `Bloodgift Demon`, `Coastal Piracy`) dentro do top
40, e nenhuma trouxe nenhuma das 11 cartas "good" do benchmark tampouco. Conferido que
não é bug do script -- print bruto do top 15 de uma das queries mostra resultados
plausíveis (`Portent`, `Foresight`, `Mind Ravel`, `Accumulated Knowledge`...), só que
nenhum deles é um dos alvos.

Teste adicional específico pra `Monastery Siege`: nem a frase reduzida ao clause
relevante sozinho (`"At the beginning of your draw step, draw an additional card, then
discard a card."`, sem o setup "As this enchantment enters, choose Khans or Dragons")
encontra a carta -- só o texto **quase completo**, incluindo a parte de escolha de modo
que não tem nada a ver com draw, encontra (rank #1). E mesmo esse texto quase-completo de
`Monastery Siege` não traz `Phyrexian Arena` junto -- confirma que não existe uma frase
compartilhada entre as duas.

**Conclusão**: a hipótese de uma frase genérica unificadora não se sustentou neste teste.
Reforça o achado já documentado na parte 6 -- o mecanismo é overlap textual bruto com o
documento de produção da carta-alvo, não compreensão de conceito, e frases mais
"naturais"/genéricas pioram (não melhoram) o resultado. Uma query por carta continua
sendo o que funciona, não o que se queria evitar.

### Decisão do usuário

Manter `"draw a card"` como está -- sem benchmark que mostre diferença sistemática clara
de qualidade (não só de rank de umas poucas cartas) entre singular/plural, não vale a
troca. Nenhuma mudança em `solver.py`/`ROLE_QUERIES` feita.

## Sessão 2026-08-21 (parte 8) -- o mesmo problema já tinha sido achado, por outra pessoa, em outro canto

O resultado do Teste B da parte 7 (nenhuma frase genérica consegue trazer um candidato
óbvio de engine de draw preta) preocupou o usuário o bastante pra suspeitar que o
problema não é de fraseado de query, e sim mais a montante (embedding/indexação em si).
Antes de seguir tentando mais queries, fui checar `notebooks/embedding_xploring.ipynb`
(notebook exploratório do Zuilho, dono do backend de AI -- ver `project_team_split`) a
pedido do usuário.

### O notebook já documenta o mesmo fenômeno, em outro par carta/query

Sem saber do trabalho desta sessão, o Zuilho tinha rodado exatamente o mesmo tipo de
teste com `Blasphemous Act` (o board wipe clássico -- "Blasphemous Act deals 13 damage to
each creature. This spell costs {1} less to cast for each creature on the battlefield.")
contra a query genérica `"deal damage to all creatures"`:

```
Blasphemous Act ficou na posição #347 de 5000.
Distância: 0.5606
Top 5 que "roubaram" o lugar: Retaliate, Forfend, Localized Destruction,
Fell the Mighty, Enchanted Being
```

Mesmo padrão exato do achado desta sessão com `Phyrexian Arena`/`"draw a card"` (rank
#1527 de 5000, partes 3-4): uma carta canônica e óbvia da categoria perde feio pra uma
query curta e genérica, batida por cartas obscuras/irrelevantes. E a correção que o
Zuilho tentou é a mesma que esta sessão descobriu de forma independente: trocar a query
genérica por uma frase quase-verbatim do oracle_text da própria carta
(`"deals a lot of damage to each creature and costs less for each creature on the
battlefield"`) traz `Blasphemous Act` pra **#2** (`Battlefield Butcher` #1). Duas pessoas,
duas cartas completamente diferentes (board wipe vs draw engine), o mesmo diagnóstico e a
mesma correção ad-hoc -- forte confirmação cruzada de que isso não é peculiaridade do
domínio "draw", é uma limitação estrutural do MiniLM + formato de documento atual
(`"{name} - {type}. Effect: {oracle_text}"`) contra queries curtas e genéricas, afetando
qualquer categoria de carta.

### O que o notebook NÃO tem

É só exploração/diagnóstico -- sem células de análise textual (markdown) e sem proposta
de correção. Depois do achado do `Blasphemous Act`, o notebook segue pra visualização:
projeção UMAP 2D de todos os embeddings (colorido por identidade de cor, por "função
mecânica" via um classificador de keyword simples parecido com `roles.py`, e por tipo
principal de carta) e K-Means com 8 clusters no espaço original de 384D. Não há nenhuma
célula conectando essa visualização de volta à pergunta "por que a carta óbvia perde pra
query curta" nem testando alternativas (outro modelo de embedding, busca híbrida
léxico+semântica, reranking, etc.) -- fica só registrado que o fenômeno existe e visto de
outro ângulo, não uma correção testada.

### Estado atual

Usuário está montando, em paralelo, um novo cenário de benchmark: comandante preto o mais
genérico/textless possível, com ~10 engines de draw preta reconhecidamente boas como
lista "good", pra ter um terceiro caso de teste (além do Esper-encantamentos existente)
que isole o problema de "achar engine de draw genérica" sem depender do tema de
encantamento. Ainda não entregue -- ficará junto do `aminatou_esper_enchantments.json` em
`eval/archetypes/` quando pronto.

## Sessão 2026-08-21 (parte 9) -- o cenário Yargle chegou, e o resultado é o pior até agora

O usuário entregou o terceiro cenário: `Yargle, Glutton of Urborg` (criatura mono-preta
com `oracle_text` **vazio** -- literalmente textless, confirmado no banco), query
`"Quero engines de draw repetíveis ou cartas que me dão card advantage."` (traduzida pro
inglês, mesmo padrão de `aminatou_esper_enchantments.json`), e 10 cartas-alvo:
`Phyrexian Arena`, `The One Ring`, `Black Market Connections`, `Morbid Opportunist`,
`Necropotence`, `Liliana, Dreadhorde General`, `Deadly Dispute`, `Night's Whisper`,
`Village Rites`, `Corrupted Conviction`. Todos os 10 nomes conferidos contra o catálogo --
sem erro de digitação. Sem `max_card_price` desta vez (usuário não pediu cap) -- nenhuma
das 10 fica de fora por preço, ao contrário do erro cometido nas partes 3/4/6 com a lista
de 8 da Aminatou. Arquivo: `eval/archetypes/yargle_black_draw_engines.json`.

Por ser textless, `self_referential_types`/`detect_known_themes` (ambos leem
`cmd.oracle_text`) não disparam nada -- esse cenário isola de vez a pergunta "a query
genérica de draw + a query do próprio usuário conseguem achar engines óbvias de draw
preto?", sem nenhuma interferência de tribal/tema.

### Resultado: 0/10, incluindo a query real do usuário

```
candidate_pool size: 220
good recall: 0/10 (0.0)
bad hit rate: 0/0 (None -- nenhuma lista "bad" fornecida)
```

Quebrado por query (das 6 que rodam de verdade nesse cenário -- a do usuário + as 5
`ROLE_QUERIES`; a query de oracle_text do comandante é descartada por estar vazia):

| Query | Hits boas | Top do resultado |
|---|---|---|
| `"I want repeatable draw engines or cards that give me card advantage."` (query real do usuário) | **0** | `Insatiable Avarice`, `Live Fast`, `Avishkar Raceway`, `Gerrard`, `Double-Faced Substitute Card`, ... |
| `"add mana ramp artifact"` | 0 | `Manalith`, `Mana Prism`, ... |
| `"draw a card"` | 0 | `Card Draw`, `Gerrard`, `Library of Alexandria`, ... |
| `"destroy target exile counter"` | 0 | `Oblivion Stone`, `Final Death`, ... |
| `"creature tokens"` | 0 | `Shapeshifter` (x3), `Insect`, ... |
| `"basic land"` | 0 | `Sovereign's Realm`, `Ash Barrens`, ... |

Nenhuma das 6 acerta uma carta sequer. O achado mais grave: **a própria query do
usuário**, em inglês fluente, articulando exatamente a intenção ("repeatable draw
engines or cards that give me card advantage"), tem zero hits e traz como topo cartas
sem relação nenhuma -- inclusive `Double-Faced Substitute Card` e
`Dominarioes // Dominarioes (cont'd)`, que parecem ser cartas de placeholder/piada do
catálogo, não staples reais.

### Por que isso é mais grave que os achados anteriores

Nas partes 3-8, o padrão sempre foi "query genérica falha, query quase-verbatim do
oracle_text da carta-alvo funciona". Esse cenário testa exatamente esse "melhor caso" --
a query do usuário já é uma frase natural e específica o bastante sobre o conceito
("repeatable... card advantage") -- e ainda assim falha completamente. Não é mais sobre
escrever queries melhores dentro do sistema atual; é evidência de que o mecanismo de
retrieval (MiniLM + formato de documento + Chroma cosine) não resolve nem o caso mais
favorável de uma pergunta comum e bem-formada. Confirma a suspeita do usuário: o
problema provavelmente está mais a montante do que fraseado de query -- alinhado com o
achado independente do Zuilho na parte 8 (mesmo fenômeno com `Blasphemous Act`).

### Estado

Nenhuma mudança de código feita. Usuário está pausando a iteração em cima de queries
específicas pra pensar no problema num nível mais estrutural.

## Sessão 2026-08-21 (parte 10) -- explorando os "porquês", 4 experimentos isolados

Pedido do usuário: em vez de continuar testando queries, entender as causas estruturais
por trás do achado da parte 9. 4 scripts descartáveis (scratchpad, não commitados),
todos usando `RAGSearcher`/MiniLM real, sem LLM.

### Overlap entre a query do usuário e `"draw a card"` (cenário Yargle, top-40 de cada)

Só **9 das 40** cartas se repetem entre as duas queries, e mesmo essas 9 mudam bastante
de posição (`Library of Alexandria`: #10 na query do usuário -> #3 em `"draw a card"`;
`Bazaar of Baghdad`: #33 -> #9). As 10 cartas-alvo quase não aparecem nem ampliando pra
top-400: só `Phyrexian Arena` (#170 na query do usuário) e `Night's Whisper` (#121 /
#64) aparecem; as outras 8 (`The One Ring`, `Black Market Connections`,
`Morbid Opportunist`, `Necropotence`, `Liliana, Dreadhorde General`, `Deadly Dispute`,
`Village Rites`, `Corrupted Conviction`) não aparecem em nenhuma das duas nem entre as
400 melhores.

### Experimento A -- remover o prefixo `"{name} - {type}. Effect: "` resolve o caso Yargle?

Reindexação isolada (11720 cartas mono-pretas/incolores, sem tocar no Chroma real):
embedar cada carta com o documento de produção (`prod`) e com **oracle_text puro**
(`oracle`), e ranquear as 10 cartas-alvo contra as 2 queries em cada formato.

| Carta | prod / query usuário | prod / "draw a card" | oracle / query usuário | oracle / "draw a card" |
|---|---|---|---|---|
| Phyrexian Arena | #325 | #1116 | #530 | **#115** |
| The One Ring | #3327 | #4259 | #3301 | #3778 |
| Black Market Connections | #2160 | #6052 | #963 | #5711 |
| Morbid Opportunist | #3463 | #1075 | #310 | #1387 |
| Necropotence | #2399 | #1963 | #637 | **#294** |
| Liliana, Dreadhorde General | #2576 | #4234 | #2442 | #2998 |
| Deadly Dispute | #3794 | #3675 | #3331 | #2767 |
| Night's Whisper | #249 | #116 | #76 | **#31** |
| Village Rites | #4155 | #3918 | #1265 | #741 |
| Corrupted Conviction | #6136 | #3958 | #1266 | #744 |

Tirar o prefixo **ajuda de verdade** (`Phyrexian Arena` #1116 -> #115, `Necropotence`
#1963 -> #294, `Village Rites`/`Corrupted Conviction` ~#3900 -> ~#740 contra
`"draw a card"`) -- confirma o achado de causa raiz da parte 3 num conjunto de cartas
totalmente novo. Mas **não é suficiente**: só `Night's Whisper` chega perto do top-40
(#31); as outras 9 continuam bem fora, mesmo sem prefixo nenhum. Ou seja, o prefixo é um
fator real, mas não é o único, nem o maior, fator no caso Yargle.

### Experimento B -- o texto residual (oracle_text puro) ainda penaliza cartas longas

Universo controlado: só cartas mono-pretas/incolores cujo `oracle_text` contém a palavra
"draw" literalmente (ou seja, relevância já garantida por regex -- a pergunta não é "é
sobre draw", é "o quão bem o embedding acha isso"). Cosine contra `"draw a card"`,
bucketado por tamanho do oracle_text:

| Tamanho (palavras) | n | cosine médio |
|---|---|---|
| 5-13 | 109 | 0.523 |
| 14-18 | 110 | 0.461 |
| 18-23 | 109 | 0.436 |
| 23-27 | 110 | 0.400 |
| 27-32 | 110 | 0.398 |
| 32-37 | 109 | 0.389 |
| 37-43 | 110 | 0.369 |
| 43-50 | 109 | 0.343 |
| 50-62 | 110 | 0.316 |
| 62-362 | 110 | 0.320 |

Correlação de Pearson (tamanho x cosine): **-0.323**. Queda monótona e suave, bucket a
bucket, de 0.52 pra 0.32 -- mesmo controlando relevância (todas as 1096 cartas contêm
"draw" literalmente). `Night's Whisper` (8 palavras) pontua 0.61; `Necropotence` (41
palavras, efeito de draw muito mais forte de fato) pontua 0.48. **O modelo não está
julgando qualidade/força do efeito -- está julgando o quanto do texto inteiro é
consumido pelo trecho que bate com a query.**

### Experimento C -- as cartas "ruído" (`Insatiable Avarice`, `Gerrard`...) são hubs universais?

Hipótese testada: será que essas cartas aparecem no topo de qualquer query, independente
de assunto (um "buraco negro" no espaço de embedding, fenômeno conhecido em espaços de
alta dimensão)? Testei as 5 cartas-ruído contra 5 queries totalmente não relacionadas a
draw (`"destroy target creature"`, `"search your library for a land card"`, etc.) --
**nenhuma delas aparece no top-40 de nenhuma query não relacionada.** Hipótese de hub
**refutada**. Além disso, essas queries curtas e diretas acertam muito bem sozinhas:
`"destroy target creature"` -> `Murder, Impale, Go for the Throat, Eviscerate, Cast
Down`; `"search your library for a land card"` -> `Ash Barrens, Vibrant Cityscape,
Terramorphic Expanse`. Ou seja, o mecanismo funciona bem pra ações simples e
templatizadas -- o problema não é o modelo em geral, é algo específico do formato da
query "draw" ou da estrutura da frase.

### Experimento D -- o problema é o assunto "draw", ou o formato da frase da query?

Teste decisivo: aplicar a mesma estrutura de frase longa e composta ("I want X or Y
cards that...") a um assunto onde já sabíamos que a query curta funciona perfeitamente
(remoção).

| Query | Top 5 |
|---|---|
| `"destroy target creature"` (curta) | Murder, Impale, Go for the Throat, Eviscerate, Cast Down |
| `"I want repeatable removal effects or cards that let me destroy creatures."` (longa, composta, mesmo tema) | Extinction, Destroy Evil, Common Black Removal, Finale of Eternity, Decimate |
| `"draw a card"` (curta) | Card Draw, Gerrard, Library of Alexandria, Illuminated Folio, Brain Pry |
| `"I want repeatable draw engines or cards that give me card advantage."` (longa, composta -- a query real do Yargle) | Insatiable Avarice, Live Fast, Avishkar Raceway, Gerrard, Double-Faced Substitute Card |

**A degradação se repete de forma quase idêntica em remoção**, um domínio onde a query
curta tinha resultado perfeito. Isso isola a causa: não é o conceito "draw" que é
mal-representado -- é que **frases longas, naturais e compostas** (do jeito que um
usuário real digita, e do jeito que o Architect provavelmente formularia uma query a
partir do prompt do usuário) sistematicamente produzem embeddings "diluídos" que não
batem bem com nenhum documento específico, non importa o assunto.

### Síntese -- os 4 eixos causais, e como se somam

1. **Prefixo do documento** (`"{name} - {type}. Effect: "`) dilui ~0.24 de cosine, fixo
   em toda carta do índice (parte 3, replicado aqui no Exp. A).
2. **Tamanho do oracle_text** dilui de forma contínua e proporcional -- correlação
   -0.32 mesmo controlando relevância (Exp. B). Cartas com efeito forte mas texto longo
   (`Necropotence`, `Liliana, Dreadhorde General`) são penalizadas por serem "engines
   completas" com mais cláusulas, não por serem piores.
3. **Tamanho/estrutura da query** tem o mesmo efeito do lado oposto -- frases longas e
   compostas (`"I want X or cards that Y"`) degradam a busca **independente do assunto**
   (Exp. D, confirmado em remoção E em draw).
4. **Não é hubness** -- não existem cartas "buraco negro" que dominam indiscriminadamente
   (Exp. C refuta). O ruído no topo é resultado direto dos eixos 2+3: quando os dois
   lados (query longa, documento longo) produzem vetores "diluídos", o que sobra no topo
   é meio-aleatório (título curto, texto reforça padrão de vocabulário difuso).

Conclusão prática: MiniLM `all-MiniLM-L6-v2` (modelo pequeno, mean-pooling, treinado
majoritariamente em pares curto-curto de STS/NLI) tem uma assimetria estrutural de
comprimento nos dois lados da busca -- documento e query. Isso explica tanto o padrão
"query curta perde pra doc longo" (parte 3) quanto o caso mais grave de todos (parte 9:
query longa do usuário perde pra qualquer coisa). Não é um bug de fraseado nem de tema;
é uma limitação de arquitetura do par (modelo de embedding, formato de documento) atual.
Nenhuma mudança de código feita nesta parte -- só investigação.

## Sessão 2026-08-21 (parte 11) -- eixo 3 na prática: decompor a query resolve sozinho?

Usuário pediu pra testar a hipótese do eixo 3 (decompor a query longa em frases curtas)
antes de qualquer implementação. Cenário: Yargle, mesmas 10 cartas-alvo.

### Tentativa 1 -- decomposição em linguagem estratégica/meta, curta

4 frases curtas só quebrando a intenção do pedido original, sem citar nenhuma
carta-alvo: `"repeatable card draw engine"`, `"card advantage"`, `"draw a card"`,
`"recurring draw effect each turn"`.

**Resultado: 0/10 na união das 4.** Surpresa -- encurtar a query sozinho não bastou.
Os tops continuam ruído (`Ring of Renewal`, `Dark Deal`, `Double-Faced Substitute
Card`...).

### Tentativa 2 -- decomposição em linguagem "estilo regras", ainda genérica

Hipótese revisada: o que importa não é só o *tamanho* da query, é o quanto ela se
parece com o *vocabulário/estrutura de oracle_text real* (como as cartas são escritas
de fato) -- termos estratégicos como "card advantage"/"engine"/"repeatable" quase nunca
aparecem literalmente em texto de carta, então nenhum tamanho de query nessa linguagem
vai bater bem. 5 frases curtas, agora no estilo de gatilho comum de regras (ainda
genéricas, não verbatim de nenhuma carta específica do benchmark):

| Frase (estilo-regras) | Hit |
|---|---|
| `"at the beginning of your upkeep, draw a card"` | `Phyrexian Arena` #30 |
| `"whenever a creature you control dies, draw a card"` | `Morbid Opportunist` #10 |
| `"sacrifice a creature or artifact, draw a card"` | (nenhum) |
| `"pay 1 life, draw a card"` | (nenhum) |
| `"whenever a permanent you control is put into a graveyard, draw a card"` | `Necropotence` #36 |

**Resultado: 3/10 na união** (`Phyrexian Arena`, `Morbid Opportunist`, `Necropotence`) --
melhora real sobre 0/10, mas ainda longe de resolver o cenário; as outras 7 cartas
(`The One Ring`, `Black Market Connections`, `Liliana, Dreadhorde General`,
`Deadly Dispute`, `Night's Whisper`, `Village Rites`, `Corrupted Conviction`) continuam
fora do top-40 mesmo com esse ajuste.

### Conclusão revisada sobre o eixo 3

Decompor a query em frases curtas **não é suficiente sozinho** -- precisa também que a
frase resultante esteja no registro/vocabulário de oracle_text (não linguagem
estratégica). Isso implica que a decomposição, se implementada (ex: pedir pro LLM
quebrar o pedido do usuário em frases curtas), precisaria gerar frases que *soam como
regras de carta*, não paráfrases do pedido do jogador -- essencialmente reinventar o
HyDE (Hypothetical Document Embeddings) citado como opção separada, não uma
decomposição simples. Reforça também o achado do Experimento B (parte 10): mesmo com
frase curta e no registro certo, ainda fica faltando alcançar boa parte das cartas --
sugerindo que o eixo 3 e o eixo 2 (diluição do lado do documento) precisam ser atacados
juntos, não um de cada vez. Nenhuma mudança de código feita.

### Nota -- a query que achou `Necropotence` na Tentativa 2 é um match parcialmente falso

Oracle_text real de `Necropotence`: `"Skip your draw step. Whenever you discard a card,
exile that card from your graveyard. Pay 1 life: Exile the top card of your library face
down. Put that card into your hand at the beginning of your next end step."` -- não tem
gatilho de "permanente indo pro cemitério", nem a palavra "draw" no efeito de compra em
si (usa "put into your hand"). A query que bateu (`"whenever a permanent you control is
put into a graveyard, draw a card"`, #36) provavelmente casou por sobreposição de
vocabulário (graveyard/discard/exile), não por match mecânico real. Enfraquece um pouco
o resultado "3/10" -- pelo menos 1 dos 3 hits parece coincidência textual, não
confirmação limpa da hipótese.

## Sessão 2026-08-21 (parte 12) -- nome vs type_line: qual custa mais caro?

Usuário topou tirar o nome do documento embedado (proper noun, quase nunca relevante
semanticamente pra uma query normal), mas achou que o type_line devia continuar
semântico. Pediu pra validar isso sem tocar no índice/código real do Zuilho -- mesma
técnica dos experimentos anteriores: reembedding local, descartável, com o modelo já
treinado, sem mexer em produção.

3 variantes de documento, universo e queries iguais aos experimentos anteriores:
`full` (produção: nome+tipo+efeito), `no_name` (tipo+efeito, sem nome),
`oracle` (só o efeito, sem nome nem tipo).

| Carta | full / "draw a card" | no_name / "draw a card" | oracle / "draw a card" |
|---|---|---|---|
| Phyrexian Arena | #1116 (0.399) | #2717 (0.472) | #2401 (0.543) |
| The One Ring | #4259 (0.279) | #4900 (0.279) | #4336 (0.317) |
| Black Market Connections | #6052 (0.209) | #5473 (0.251) | #5755 (0.244) |
| Morbid Opportunist | #1075 (0.401) | #3185 (0.393) | #3151 (0.395) |
| Necropotence | #1963 (0.364) | #3089 (0.403) | #2550 (0.481) |
| Liliana, Dreadhorde General | #4234 (0.280) | #4007 (0.328) | #3909 (0.340) |
| Deadly Dispute | #3675 (0.302) | #4285 (0.312) | #3789 (0.346) |
| Night's Whisper | #116 (0.481) | #2376 (0.494) | #30 (0.613) |
| Village Rites | #3918 (0.293) | #3237 (0.388) | #2833 (0.429) |
| Corrupted Conviction | #3958 (0.291) | #3236 (0.388) | #2836 (0.429) |

Cosine médio (query `"draw a card"`, 10 alvos): full=0.330, no_name=0.371, oracle=0.414 --
o **score sobe de forma monótona** conforme se tira nome e depois tipo, como esperado.

### Achado contra-intuitivo: score sobe, mas o rank nem sempre melhora

Olhando por carta, `no_name` (só tira o nome) **piora o rank** de metade dos alvos em
relação a `full` -- `Phyrexian Arena` #1116 -> #2717, `Morbid Opportunist` #1075 -> #3185,
`Necropotence` #1963 -> #3089, `Night's Whisper` #116 -> #2376 -- mesmo com o cosine
absoluto subindo em todos os casos. Só a outra metade melhora (`Village Rites`,
`Corrupted Conviction`, `Black Market Connections`, `Liliana`).

Explicação: rank é competição, não limiar absoluto -- **todas as ~11720 cartas do
universo também mudam de score** quando o documento muda de formato, não só as 10
cartas-alvo. Hipótese pra explicar a direção do efeito: tirar o nome encurta o
documento, e isso aumenta a **fração relativa** que o `type_line` ocupa no texto que
sobra -- ou seja, o custo de diluição por token do tipo *aumenta* proporcionalmente
quando o nome sai e o tipo fica. Cartas cujo tipo é um termo muito genérico e comum
(`Sorcery`, `Enchantment`) sofrem mais desse efeito colateral do que ganham por perder o
nome. Resultado prático: manter o type_line semântico **não é uma vitória garantida** --
em ~metade dos casos testados aqui, tirar só o nome piorou o resultado. `oracle` (tira os
dois) é a variante mais consistentemente forte das três, confirmando de novo o achado do
Experimento A/B (parte 10): o efeito dominante é o comprimento total do texto, e
qualquer coisa que sobra genérica (nome OU tipo) paga um preço proporcional ao que resta.

Nenhuma mudança em produção -- reembedding local, descartável, mesmo modelo já treinado.

## Sessão 2026-08-21 (parte 13) -- BM25 léxico testado de verdade (eixo 4)

Usuário pediu explicação de reranker e BM25 (não entendia os conceitos). Depois de
explicar, testei BM25 puro (implementação própria em Python, `rank_bm25` não está
instalado no venv -- `k1=1.5, b=0.75`, parâmetros padrão) contra o `oracle_text` cru das
11720 cartas mono-pretas/incolores, mesmas 2 queries do cenário Yargle. Zero embedding
envolvido nesse experimento.

### Reranker -- esclarecido, não testado (limite estrutural, não vale testar ainda)

Cross-encoder só reordena candidatos que **já foram buscados** -- não alcança cartas fora
do conjunto pré-filtrado. Como 8 das 10 cartas-alvo não aparecem nem no top-2000/6000 de
nenhuma variante de embedding testada, um reranker não teria chance de vê-las a menos que
o primeiro estágio já buscasse uma janela enorme (o que anula o ganho de performance de
rodar reranker só sobre um pool pequeno). Utilidade real só pras 2 cartas que já ficam
"pertinho" (`Phyrexian Arena`, `Night's Whisper`, rank ~30-170 no melhor formato) --
não testado ainda, fica registrado como não-prioritário por ora.

### BM25 -- resultado misto, mas revelador

| Carta | BM25 rank (query usuário) | BM25 rank (`"draw a card"`) |
|---|---|---|
| Phyrexian Arena | #865 | #180 |
| The One Ring | #3114 | #918 |
| Black Market Connections | #1599 | #657 |
| Morbid Opportunist | #220 | #253 |
| Necropotence | #270 | #446 |
| Liliana, Dreadhorde General | #2350 | #540 |
| Deadly Dispute | #629 | #1291 |
| Night's Whisper | #134 | #688 |
| Village Rites | #408 | #593 |
| Corrupted Conviction | #413 | #597 |

**Nenhuma das 10 entra no top-40 via BM25 sozinho** -- mesmo teto de antes. Mas metade
das cartas (`Morbid Opportunist`, `Necropotence`, `Village Rites`,
`Corrupted Conviction`) fica **muito mais perto** do top-40 do que qualquer variante de
embedding testada (rank ~200-600 via BM25 vs ~2000-4000 via embedding). A outra metade
(`The One Ring`, `Liliana`, `Black Market Connections`) continua ruim nos dois eixos --
hipótese: o efeito de draw dessas cartas é descrito com vocabulário menos literal/direto
("draw" não é a palavra dominante do texto).

### Conclusão -- os dois eixos falham em cartas diferentes, não nas mesmas

BM25 e embedding erram metades diferentes do conjunto de 10 -- evidência a favor de somar
os dois scores (híbrido) em vez de escolher um só. Nenhum dos dois, sozinho, fecha o
cenário Yargle completo. Nenhuma mudança de código feita -- script descartável, sem
libs externas, sem tocar em produção.

## Sessão 2026-08-21 (parte 14) -- publicação do relatório + merge com o Zuilho

Duas coisas fechadas nesta parte:

1. **Relatório público**: todo o conteúdo relevante das partes 1-13 (o que foi feito, o
   que deu certo/errado, o que não refazer) foi condensado em `eval/RETRIEVAL_FINDINGS.md`
   (tracked, pushado) -- versão enxuta pra consumo de qualquer um no time, sem a narrativa
   passo-a-passo que só interessa a esta sessão. Este arquivo (`eval-harness-plan.md`)
   continua sendo o rascunho de trabalho completo, não pushado (`lucas_plans/` no
   `.gitignore`).
2. **Merge com o branch do Zuilho** (commit `2af638d`, via "Sync Changes" do VSCode --
   `git pull` com estratégia `ort`): sem conflito nenhum (confirmado antes com
   `git merge-tree`, e depois in loco). Achado: o ponto de divergência real
   (`f946d00`) é anterior a todos os commits desta sessão, mas o commit gigante do Zuilho
   ("modifications") já carregava uma cópia do meu trabalho até `d267764` por baixo --
   por isso o merge resolveu liso mesmo com o grafo parecendo bagunçado.

   Mudança dele que afeta diretamente esta investigação: `src/archetypes.py` (novo) --
   `infer_archetype(query)` classifica a query num de 12 arquétipos, cada um com seu
   próprio `search_queries`. Isso **substitui** o `ROLE_QUERIES` fixo que foi o objeto
   central das partes 3-13. Re-rodei `scripts/eval_retrieval.py` nos dois cenários depois
   do merge:

   | Cenário | Antes do merge | Depois do merge |
   |---|---|---|
   | Aminatou (classificada `control` por causa da palavra "counterspells" na query) | recall 30%, bad rate 90% | recall 45.5%, bad rate 30% |
   | Yargle (fica `generic`, nenhuma keyword de arquétipo bate) | recall 0%, pool 220 | recall 0%, pool 151 (menos queries que o `ROLE_QUERIES` antigo) |

   Melhora real na Aminatou, mas é efeito colateral da classificação de arquétipo bater
   com uma palavra da query, não conserto do problema estrutural documentado nas partes
   10-13 (os 4 eixos de diluição). Yargle continua 0/10 -- confirma que os achados sobre a
   causa raiz (`RETRIEVAL_FINDINGS.md` §4) continuam valendo, o `archetypes.py` só mudou
   qual conjunto de queries dispara, não como o MiniLM pontua cada query.

   Documentação atualizada pra refletir isso: `RETRIEVAL_FINDINGS.md` §2 (baseline nova)
   e §6 (nova seção explicando o merge), `scripts/eval_retrieval.py` (docstring não citava
   mais `ROLE_QUERIES`, que não existe mais).

   Testes: suíte completa rodada depois do merge, `72 tests ... OK`.
