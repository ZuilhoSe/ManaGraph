# Plano de melhoria do motor de montagem de decks

> **Documento detalhado do roadmap:** a ordem oficial agora está em
> [`ontology-first-roadmap.md`](ontology-first-roadmap.md). Este arquivo mantém
> os detalhes de dados, modelos e integração; `ONTOLOGY.md` é o contrato da
> representação funcional e o Stage 3.6 precisa passar antes do TDA.

## Objetivo

Evoluir o ManaGraph de um sistema que recupera cartas relevantes para um
sistema que:

- entende requisitos do deck;
- seleciona cartas sob restricoes reais;
- aprende padroes de compatibilidade;
- descobre combinacoes composicionais;
- separa popularidade de sinergia;
- propoe linhas pouco exploradas;
- continua deterministico, legal e explicavel.

O modelo nao sera a autoridade final. O catalogo, os dados Oracle, as regras
de Commander, os atributos simbolicos e o solver continuam sendo as fontes de
verdade.

## Principio central

Nao comecar treinando um LLM diretamente com listas do Moxfield ou do EDHREC.
Isso tende a ensinar:

- popularidade;
- coocorrencia;
- decks copiados;
- preferencias da comunidade;
- vieses de disponibilidade e visibilidade.

Esses dados sao uteis como prior de compatibilidade, mas nao sao prova de que
uma carta pertence ao deck ou de que uma combinacao e boa.

```mermaid
flowchart TD
    oracle[Scryfall Oracle] --> facts[Atributos simbolicos]
    decklists[Decklists autorizadas] --> cooccurrence[Coocorrencia e priors]
    edhrec[Dados agregados] --> cooccurrence
    facts --> graph[Grafo de efeitos]
    cooccurrence --> graph
    request[Requisitos do usuario] --> retrieval[Retrieval de candidatos]
    graph --> retrieval
    retrieval --> reranker[Modelo de compatibilidade]
    reranker --> solver[Solver deterministico]
    solver --> validator[Validacao de regras]
    validator --> result[Deck e explicacao]
```

## Papel de cada fonte

### Scryfall e Oracle

Devem ser a fonte factual para:

- nome e identidade canonica;
- Oracle text;
- type line;
- mana cost e mana value;
- keywords;
- legalidades;
- cor e identidade de cor;
- preco, quando aplicavel.

O Oracle deve alimentar os predicados simbolicos. O texto Oracle nao deve ser
substituido por uma classificacao livre do LLM.

### Forge

O [Forge](https://github.com/Card-Forge/forge) entra como fonte de bootstrap e
validacao da ontologia. Uma release fixada de `cardsfolder` pode fornecer
efeitos, custos, eventos, `DeckHas` e `DeckNeeds` para gerar candidatos a
objetos, predicates e relacoes antes da anotacao do catalogo.

Esses dados nao substituem Scryfall/Oracle e nao viram dependencia de runtime.
O mapeamento deve ser validado contra um conjunto rotulado manualmente,
registrar divergencias de Oracle e manter os artefatos derivados de Forge como
validacao interna. A taxonomia de arquetipos do Forge nao deve ser importada
para o schema mecanico.

### Moxfield

Pode ser util para:

- exemplos de decks completos;
- coocorrencia entre cartas;
- curva real;
- composicao por comandante;
- cartas usadas em arquetipos especificos;
- dados temporais, caso obtidos de forma autorizada.

Limitacoes:

- decks podem ser copias ou derivados de listas populares;
- uma carta aparecer em um deck nao prova que ela e boa;
- cartas pouco usadas ficam sub-representadas;
- listas podem conter erros, cartas ilegais ou sideboards;
- scraping e redistribuicao podem ser proibidos pelos termos de uso.

Usar somente dados obtidos de forma autorizada e registrar a origem e a
licenca de cada dataset.

### EDHREC

Pode ser util para:

- frequencia por comandante;
- frequencia por tema;
- co-inclusao;
- recomendacoes agregadas;
- separacao entre staples e cartas especificas de um tema.

Limitacoes:

- forte efeito de popularidade;
- recomendacoes que reforcam as mesmas escolhas;
- sinergia nao e avaliacao causal;
- decks populares influenciam recomendacoes futuras;
- ausencia de uma carta nao e evidencia forte contra ela.

EDHREC deve ser tratado como um prior fraco ou uma feature de ranking, nunca
como label absoluto.

## Representacao simbolica de cartas

Antes de treinar modelos, consolidar uma camada de atributos em
`src/symbolic_cards.py`.

Cada carta deve poder expor:

```json
{
  "name": "Card Name",
  "type_line": "Enchantment",
  "mana_value": 3,
  "color_identity": ["W", "U"],
  "roles": ["draw", "engine"],
  "effects": [
    {
      "family": "draw",
      "trigger": "enchantment_enters",
      "target": "controller",
      "amount": 1
    }
  ],
  "attributes": {
    "is_land": false,
    "is_counter": false,
    "is_draw": true,
    "is_ramp": false,
    "is_protection": false,
    "is_extra_combat": false,
    "is_removal": false
  },
  "attribute_evidence": {
    "is_draw": {
      "origin": "oracle_rule",
      "confidence": 1.0
    }
  }
}
```

### Familias iniciais

- draw;
- counter;
- removal;
- bounce;
- ramp;
- protection;
- extra combat;
- combat damage;
- evasion;
- tutor;
- sacrifice;
- death trigger;
- tokens;
- token payoff;
- graveyard;
- recursion;
- enchantment payoff;
- artifact payoff;
- landfall;
- blink;
- copy;
- spell recursion;
- cost reduction;
- alternate win condition.

Uma carta pode ter multiplos papeis e multiplas familias de efeito.

### Origem e confianca

Cada atributo deve registrar sua origem:

- `oracle_rule`: derivado por regra deterministica;
- `catalog_field`: veio diretamente do catalogo;
- `curated_rule`: regra revisada manualmente;
- `assisted_offline`: sugestao produzida por LLM, ainda nao aprovada.

Somente `oracle_rule`, `catalog_field` e `curated_rule` podem afetar a
selecao de producao. Uma classificacao assistida pelo LLM precisa passar por
revisao antes de entrar no catalogo confiavel.

## Workstream 1 — Baseline estatistico e observacao

Este workstream apoia o Stage 3.6 e nao substitui a ontologia. Coocorrencia,
PMI e dados de Moxfield/EDHREC devem permanecer separados do score funcional
ate serem normalizados por contexto.

Criar primeiro uma referencia sem rede neural.

### Coocorrencia

Calcular:

- quantidade de decks que contem as duas cartas;
- frequencia da carta no comandante;
- frequencia condicional por arquetipo;
- frequencia condicional por tema;
- frequencia por faixa de mana;
- frequencia por papel.

Nao usar apenas contagem bruta, pois ela favorece staples universais.

### PMI e associacao condicional

Para cartas `A` e `B`:

```text
PMI(A, B) = log(P(A,B) / (P(A) * P(B)))
```

Tambem calcular:

```text
PMI(A, B | commander)
PMI(A, B | archetype)
PMI(A, B | requirement_family)
```

Isso separa cartas universais de cartas particularmente compativeis com um
contexto.

### Baseline de ranking

O primeiro ranking pode ser:

```text
score =
  oracle_similarity
  + symbolic_role_fit
  + requirement_fit
  + conditional_pmi
  + curve_fit
  + mana_fit
  + inventory_fit
  - redundancy
  - legality_risk
```

Popularidade deve entrar com peso pequeno e separado:

```text
score_total = score_functional + 0.1 * popularity_prior
```

O objetivo e impedir que EDHREC ou Moxfield transforme o sistema em um
recomendador de staples.

## Workstream 2 — Dataset de compatibilidade

O exemplo de treinamento deve representar contexto, nao apenas uma carta:

```json
{
  "commander": "Commander Name",
  "deck_context": ["Existing Card A", "Existing Card B"],
  "archetype": "control",
  "requirements": ["counter", "draw"],
  "candidate": "Candidate Card",
  "label": 1,
  "source": "deck_cooccurrence",
  "source_weight": 0.7
}
```

### Exemplos positivos

- duas cartas que aparecem juntas em decks do mesmo comandante;
- carta recomendada para um comandante e compativel com o Oracle;
- carta que satisfaz uma lacuna simbolica;
- combinacao presente em decks de qualidade;
- combinacao validada manualmente.

### Exemplos negativos

Nao considerar toda carta ausente como negativa. A ausencia pode significar que
ninguem a testou.

Usar negativos dificeis:

- carta da mesma cor, mas com familia de efeito incompativel;
- carta do mesmo papel, mas fora da curva;
- carta semanticamente parecida, mas com trigger errado;
- carta legal que nao satisfaz o requisito;
- carta que cria redundancia excessiva;
- carta popular em decks gerais, mas incompativel com aquele arquetipo.

### Pesos por origem

Uma politica inicial:

```text
label manual revisado          1.00
combo validado simbolicamente  0.95
coocorrencia especifica        0.80
recomendacao EDHREC            0.60
coocorrencia global            0.45
```

Os pesos sao pontos de partida e devem ser calibrados pelo benchmark.

## Workstream 3 — Modelo de compatibilidade

Treinar primeiro um modelo pequeno para:

```text
(commander, deck_context, candidate_card, requirements)
    -> compatibility_score
```

Features importantes:

- identidade de cor;
- type line;
- mana value;
- Oracle e keywords;
- papeis simbolicos;
- familias de efeitos;
- compatibilidade com o comandante;
- coocorrencia condicional;
- curva e necessidades do deck;
- disponibilidade e preco;
- popularidade como feature fraca.

Um modelo de gradient boosting ou MLP provavelmente sera suficiente no inicio.
Nao ha necessidade de fine-tunar um LLM para essa etapa.

## Workstream 4 — Retrieval contrastivo

Depois do baseline, treinar embeddings com pares:

- positivo: cartas que aparecem no mesmo deck;
- positivo forte: cartas recomendadas para o mesmo comandante;
- negativo dificil: mesma cor e mesmo papel, mas sem compatibilidade;
- negativo semantico: Oracle parecido, mas requisito diferente.

Uma representacao de carta deve combinar:

```text
card_id
type_line
mana_cost
color_identity
oracle_text
keywords
symbolic_roles
effect_families
```

O embedding descobre candidatos. O Solver continua decidindo.

## Workstream 5 — Descoberta de combinacoes novas

Listas historicas sozinhas nao conseguem ensinar combinacoes ausentes delas.
Para gerar hipoteses novas:

1. Classificar cartas por efeitos simbolicos.
2. Construir um grafo de efeitos e dependencias.
3. Procurar caminhos complementares no grafo.
4. Gerar combinacoes mesmo sem coocorrencia historica.
5. Validar identidade, legalidade, timing, custos e loops.
6. Pontuar forca, consistencia, custo e numero de pecas.
7. Medir novidade em relacao ao dataset.
8. Submeter as melhores hipoteses a revisao humana.

Exemplo de caminho:

```text
produz Treasure
    -> recompensa criacao de artefato
    -> transforma artefato em compra
    -> recompensa compras
    -> converte vantagem em dano ou condicao de vitoria
```

Uma combinacao "nova" deve ser definida como nao observada no dataset, e nao
como garantia de que nenhum jogador jamais pensou nela.

## Dataset e prevencao de vazamento

Criar divisao por:

- tempo;
- commander nao visto no treino;
- carta nao vista no treino;
- arquetipo nao visto;
- combinacao removida artificialmente;
- deck parcialmente observado.

Uma divisao aleatoria por cartas permite memorizar listas e produz metricas
enganosamente altas.

## Metricas

Medir separadamente:

- Recall@K;
- NDCG;
- legalidade;
- cobertura de papeis;
- qualidade da curva;
- cobertura de mana;
- taxa de cartas populares;
- taxa de cartas novas;
- quantidade de combinacoes ausentes do dataset;
- aceitacao humana;
- melhoria sobre o baseline atual.

Metricas de novidade:

```text
novelty@K =
  candidatos nao observados no contexto
  com validacao simbolica
  e aceitacao humana
```

Uma recomendacao so deve contar como descoberta util se continuar legal,
compativel e explicavel.

## Integracao com o ManaGraph

Arquivos e responsabilidades:

- `src/catalog.py`: fatos canonicos e identidade;
- `src/symbolic_cards.py`: familias e atributos de efeitos;
- `src/retrieval_text.py`: descoberta lexical;
- `src/hybrid_search.py`: combinacao lexical e semantica;
- `src/solver.py`: selecao sob restricoes;
- `src/manager_core.py`: aplicacao atomica e autoridade;
- `scripts/eval_selection.py`: benchmark de deck completo;
- `tests/`: contratos, determinismo e regressao.

### Coleta publica sem deck especifico

O CLI tambem pode iniciar a coleta sem comandante, deck ou URL fornecida pelo
usuario:

- Moxfield percorre a busca publica paginada e tenta enriquecer cada resumo
  com o deck publico correspondente; o ID vem de `publicId`/`publicUrl`, e o
  detalhe tenta v3 e depois v2 somente apos um HTTP 404;
- EDHREC coleta a listagem/top global e segue os comandantes descobertos,
  dentro de limites configuraveis;
- cada pagina e resposta e identificada por hash no cache, enquanto decks,
  cartas, recomendacoes, coocorrencias e proveniencia sao persistidos no
  SQLite; documentos de fonte podem ser replicados em colecoes aditivas do
  Chroma;
- endpoints, pagina, limites, intervalo, retries e modo somente-resumo sao
  configuraveis no `scripts/collect_data.py`; 401/403 nao acionam fallback.

Para EDHREC, a implementacao tambem normaliza o formato publico
`container.json_dict.cardlists[].cardviews[]`: categoria/tag, rank, inclusao,
universo potencial, percentual, sinergia, salt e metadados ficam em colunas
proprias de `recommendations`, com o JSON bruto preservado. Cada recomendacao
de uma pagina de comandante gera uma relacao
`commander_recommendation` em `cooccurrence`, alem de aceitar pares/combos
explicitos quando presentes. A migracao do esquema e aditiva (versao 3) e
nao altera a tabela legada `cards` nem a colecao `oracle_cards` do Chroma.

Essas fontes nao oferecem API publica oficial. Os endpoints Moxfield sao
interfaces nao documentadas e sujeitas a robots.txt, rate limits e mudancas.
O JSON do EDHREC tambem nao e um export/API autorizado; nao existe fallback
para HTML, `__NEXT_DATA__`, proxy, credencial ou automacao de navegador. Um
401/403 deve ficar claro como falha e a coleta deve usar fixture/export
autorizado ou URL explicitamente autorizada pelo operador. Falhas de um
detalhe individual nao invalidam o resumo coletado; a legalidade continua
dependendo exclusivamente do Oracle/catalogo e do validador deterministico.

Fluxo recomendado:

```text
pedido do usuario
  -> IntentSpec
  -> requisitos simbolicos
  -> retrieval de candidatos
  -> modelo de compatibilidade
  -> solver
  -> validacao deterministica
  -> explicacao final
```

O rationale gerado pelo LLM nunca deve alterar score, estado ou legalidade.

## Ordem de implementacao

A ordem abaixo segue [`ontology-first-roadmap.md`](ontology-first-roadmap.md):

1. Fixar uma release do Forge e implementar a mineração de `cardsfolder`.
2. Congelar `schema_v1.yaml`, consumidores e testes de aceitação do Stage 3.6.
3. Implementar extração Tier 1/Tier 2 e medir cobertura por predicate.
4. Validar predicates antes de usá-los em score, corte ou constraint.
5. Construir o grafo de supply/demand e integrar diagnóstico tipado ao solver.
6. Rodar as ablações do harness do Lucas e confirmar mudança explicável em
   fill/cut.
7. Implementar o baseline estatístico e PMI como observação contextual.
8. Adicionar Moxfield/EDHREC como priors fracos e auditáveis.
9. Treinar o modelo de compatibilidade e, depois, retrieval contrastivo.
10. Criar busca composicional e medir novidade funcional.
11. Só depois iniciar TDA, epidemiologia e modelos maiores.

## Criterios de sucesso

O motor deve:

- gerar o mesmo resultado com a mesma entrada normalizada;
- nunca incluir carta ilegal por influencia do modelo;
- melhorar o baseline sem depender apenas de popularidade;
- manter cobertura de papeis, mana e curva;
- produzir candidatos novos sem aceitar combinacoes nao validadas;
- explicar por que cada carta foi selecionada;
- permitir auditar a origem de cada recomendacao.

O primeiro marco pratico e comparar tres sistemas no mesmo benchmark:

1. retrieval atual;
2. baseline de coocorrencia/PMI;
3. modelo de compatibilidade com atributos simbolicos.

Somente depois dessa comparacao deve-se decidir se fine-tuning de embeddings
traz ganho real.
