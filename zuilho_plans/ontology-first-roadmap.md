# Roadmap do ManaGraph — Ontologia primeiro

## Decisão arquitetural

`ONTOLOGY.md` passa a ser o plano técnico central do motor. Ele não é uma
taxonomia auxiliar: define a representação simbólica que o solver usará para
diagnosticar, preencher e cortar decks.

Os documentos têm papéis diferentes:

- [`RESEARCH.md`](../RESEARCH.md) é o contrato científico e define o que pode
  ser afirmado no trabalho.
- [`ONTOLOGY.md`](../ONTOLOGY.md) é o contrato da representação funcional e
  define o desbloqueio do Stage 4.
- [`deck-engine-improvement-plan.md`](deck-engine-improvement-plan.md) detalha
  o produto do solver, fontes de dados e modelos posteriores.
- `lucas_plans/eval-harness-plan.md` é o plano independente de avaliação,
  regressão e replay de prompts.

O erro a evitar é tratar Moxfield/EDHREC como o núcleo do aprendizado. Eles
observam escolhas da comunidade; não explicam por que uma carta funciona.
Scryfall/Oracle fornece os fatos, Forge fornece um corpus mecânico para
bootstrap e validação, a ontologia transforma fatos em estrutura e o solver
decide sob restrições.

## Ordem oficial de execução

| Ordem | Stage | Entrega | Gate |
|---|---|---|---|
| 0 | Contrato e snapshot | catálogo, preços, modelo e inventário congelados | execução reproduzível |
| 1 | `DeckState` | deck simbólico, invariantes e autoridade determinística | deck legal e ≤99 |
| 2 | Fill/cut | seleção gulosa atual, depois otimização | preencher/cortar sem violar restrições |
| 3 | Geometria | embeddings, views e retrieval | baseline mensurável |
| 3.5 | Mana, curva e papéis | diagnóstico e termos simbólicos já existentes | solver consome os números |
| **3.6** | **Ontologia funcional** | predicates, relações e fluxo tipado | dois testes de aceitação passam |
| 4 | TDA | topologia sobre grafo/representação tipada | somente após Stage 3.6 |
| 5 | Epidemiologia | propagação no grafo e comparação com observações | não bloqueia o solver |
| 6 | Interface | visualização, exportação e operação | demo depois do objeto estável |

O Stage 3.6 é a próxima fronteira do motor. TDA não deve começar enquanto o
score ainda confundir semelhança textual com complementaridade mecânica.

## Stage 3.6 — plano de execução

### A. Contrato e schema

1. Fixar uma release do [Forge](https://github.com/Card-Forge/forge) e registrar
   a versão em `catalog_meta`.
2. Criar `data/ontology/schema_v1.yaml` com objetos, eventos e predicates,
   usando o vocabulário de eventos do Forge apenas como material de bootstrap.
3. Criar `src/ontology/schema.py` com enums e dataclasses.
4. Para cada predicate, registrar o consumidor:
   `solver._score_parts`, `rules_validator` ou `diagnose_deck_json`.
5. Criar `tests/test_ontology_acceptance.py` com os testes inicialmente
   falhando.
6. Tornar a versão do schema, labels, release Forge e snapshot parte da
   proveniência.

Não anotar cartas antes de congelar esse contrato.

### B. Extração em três camadas

0. **Tier 0 — Forge:** executar `scripts/mine_forge.py` sobre a release fixada
   para produzir efeitos, custos, eventos e candidatos a predicates em um
   artefato intermediário. O resultado é scaffolding e validação, não uma
   taxonomia importada nem uma dependência de runtime.
1. **Tier 1:** campos estruturados do Scryfall (`keywords`,
   `produced_mana`, `type_line`, custo e tipos).
2. **Tier 2:** gramática determinística de templates Oracle em
   `src/ontology/patterns.py`.
3. **Tier 3:** annotator offline opcional para o resíduo, produzindo
   `data/ontology/labels_v1.jsonl` congelado com modelo, hash do prompt e data.

O LLM pode propor labels durante a preparação offline; nunca deve anotar em
runtime nem alterar diretamente legalidade ou score.

### C. Validação

1. Criar um gold set estratificado de 300–500 cartas, começando por uma amostra
   manual cega ao Forge.
2. Criar `data/ontology/forge_mapping.yaml` para mapear o DSL do Forge aos
   predicates mecânicos do schema.
3. Executar o mapeamento sobre `cardsfolder` e gerar
   `data/ontology/gold_forge.jsonl` apenas como corpus interno de comparação.
4. Usar Forge para validar precisão da tradução e relações `DeckHas`/`DeckNeeds`;
   nunca importar seus nomes de arquétipo para o schema e nunca tratar ausência
   de uma tag como evidência negativa.
5. Medir precisão e recall por predicate, reportando hand-vs-Forge,
   hand-vs-pipeline e Forge-vs-pipeline.
6. Desligar predicates que não atingirem o limiar do consumidor, sem apagar o
   predicate do schema.

Limiares mínimos:

- constraint/validator: 0.98 de precisão;
- decisão de corte: 0.90;
- termo de score: 0.80;
- texto de diagnóstico: 0.70.

### D. Consumo pelo solver

Implementar, nesta ordem:

1. `src/ontology/graph.py` para supply/demand e relações derivadas;
2. usar os labels Forge somente como teste de consistência durante a construção
   do grafo; o runtime deve depender de labels congelados do pipeline;
3. diagnóstico de órfãos, consumidores sem recurso, cartas mortas e respostas
   ausentes;
4. matching saturante de recursos e eventos em `solver._score_parts`;
5. redundância por assinatura de predicate, substituindo densidade kNN para
   decisões de corte;
6. filtros `emits=`, `rewards=`, `answers=`, `enables=` em `search_cards`;
7. vetor multi-hot de predicates como view offline;
8. prompt do Architect consumindo déficits tipados, sem calcular contagens.

O objetivo não é criar uma ontologia bonita. É fazer o deck mudar de forma
explicável quando falta um produtor, consumidor, resposta ou ponte.

### E. Avaliação e ablação

Manter o harness do Lucas como camada de medição, adicionando as condições:

| Condição | Retrieval | Score | Cut |
|---|---|---|---|
| A | MiniLM concat | termos 3.5 | densidade kNN |
| B | multi-view | termos 3.5 | densidade kNN |
| C | multi-view | matching da ontologia | densidade kNN |
| D | multi-view | matching da ontologia | redundância por predicate |
| E | filtros da ontologia | ontologia | redundância por predicate |

As métricas mínimas são legalidade, cobertura de papéis, cobertura de mana,
matched-pair coverage, taxa de órfãos, taxa de consumidores sem recurso, taxa
de cartas mortas e taxa de novidades funcionais.

## Papel dos dados externos

### Scryfall/Oracle — autoridade factual

Fonte de nome canônico, Oracle Text, identidade, custo, tipos, keywords,
legalidades e preços congelados. Alimenta a extração dos predicates e não deve
ser substituída por classificação livre.

### Forge — bootstrap e validação mecânica

O [Forge](https://github.com/Card-Forge/forge) será usado para minerar a
`cardsfolder` de uma release fixada. Seus scripts fornecem uma DSL explícita de
efeitos, custos, triggers, `DeckHas` e `DeckNeeds`, útil para gerar candidatos a
objetos, eventos e predicates e para validar a tradução do pipeline.

Forge não será a fonte final dos fatos, não será executado pelo solver e não
terá sua taxonomia de arquétipos importada para o schema. O pipeline cruza
Forge com Scryfall pelo nome e Oracle Text, registra divergências e mantém
artefatos derivados de Forge como validação interna, respeitando a licença do
projeto e sem redistribuir o corpus derivado.

### Moxfield — observação de decks

Pode fornecer exemplos, coocorrência, curva e composição por comandante. A
coocorrência é um prior observacional e deve ficar separada do score funcional
até ser normalizada por contexto.

### EDHREC — prior fraco e contexto

Inclusão, sinergia publicada, categorias, decks e cartas cortadas são sinais
úteis para análise e contraste. Não são labels de qualidade nem prova causal.
Popularidade deve ser uma feature pequena, separada e auditável; ausência não
deve ser tratada como negativo.

O pipeline deve conseguir produzir uma combinação nova sem depender de ela ter
aparecido no Moxfield ou EDHREC. Essa é a razão para a ontologia vir antes do
modelo de compatibilidade.

## Dependências entre os planos

### Trabalho do Zuílho

- congelar schema e consumidores;
- implementar extração e grafo funcional;
- integrar predicates ao diagnóstico e solver;
- manter autoridade de legalidade, orçamento e estado;
- decidir quando a ontologia está pronta para TDA.

### Trabalho do Lucas

- proteger regressões determinísticas do solver;
- medir retrieval e seleção separadamente;
- manter cenários de replay de prompts;
- executar ablações A–E;
- reportar novidade versus popularidade, não overlap com EDHREC como objetivo.

O trabalho de avaliação pode começar com fixtures e testes de contrato, mas não
deve transformar o comportamento atual em alvo de otimização. O alvo é
legalidade, coerência funcional, explicabilidade e descoberta.

## Próximas tarefas

1. Fixar uma release do Forge e seu hash/identificador em `catalog_meta`.
2. Criar `scripts/mine_forge.py` e testar o parser em fixtures pequenas.
3. Criar o schema v1 e a matriz predicate → consumidor usando Forge apenas
   como bootstrap.
4. Escrever os dois testes de aceitação como testes falhando.
5. Implementar Tier 1 e Tier 2 com fixtures de cartas reais.
6. Gerar `gold_forge.jsonl` e a primeira tabela de cobertura por predicate.
7. Implementar o grafo de supply/demand.
8. Fazer o diagnóstico tipado alimentar o solver e o Architect.
9. Rodar as ablações antes de iniciar TDA.

## Critérios de parada

- Se um predicate não tiver consumidor, ele não entra na implementação.
- Se Tier 1 + Tier 2 não cobrirem os predicates prioritários até o fim da
  validação inicial, reduzir o schema ao conjunto consumido pelo solver.
- Se o Stage 3.6 não mudar decisões de cut/fill de maneira explicável, ele
  produziu apenas uma taxonomia e deve ser refeito antes de TDA.
- Se dados externos forem bloqueados, o motor deve continuar funcional com
  Scryfall, fixtures e o grafo simbólico; fontes externas nunca podem ser um
  requisito de runtime.
