## Development Roadmap

Research contract: [RESEARCH.md](RESEARCH.md). Stage 2 fill/cut is in the graph (`Architect → Inventory → Solver → Supervisor`).

This project is built incrementally: first a deterministic local data layer, then autonomous agent orchestration, then optional topological synergy analysis.

### Epic 1: Data Foundation and Inventory

Local infrastructure to read cards and game rules.

- [x] **Scryfall integration:** Download and process Scryfall bulk data (JSON), extracting Oracle Text, Mana Value, Color Identity, and Legalities.
- [x] **Inventory parser:** Module that reads a personal collection (ManaBox/Moxfield-style lists).
- [x] **Relational database:** SQLite tracks collection state (which cards are free, which are allocated to decks).
- [x] **Base rules validator:** Deterministic Python checks for commander color identity and the singleton restriction.

### Epic 2: RAG Pipeline and Vector Space

The system understands cards semantically, beyond keyword search.

- [x] **Embedding generation:** Use `sentence-transformers` (HuggingFace) to turn Oracle Text into dense vectors.
- [x] **ChromaDB/FAISS setup:** Store Scryfall card embeddings and create partitions for the local inventory.
- [x] **Semantic retrieval:** Given a query (e.g. "protect commander from board wipes"), return the N most relevant cards by cosine similarity.
- [x] **Hybrid filtering:** Combine vector search (RAG) with strict SQL filters (e.g. similar cards only if Color Identity == Izzet and inventory > 0).

### Epic 3: Multi-Agent Orchestration (LangGraph / CrewAI)

The core intelligence. Agents get tools to interact with the data layer from the previous epics.

- [x] **LLM configuration:** Gemini (or a local model) as the reasoning engine.
- [x] **Tools:** Wrap Epic 1 and 2 functions as LangChain `@tool`s.
- [x] **Inventory manager agent:** Check availability and move cards between decks.
- [x] **Architect agent (RAG):** Discover synergies and query rules in the vector DB.
- [x] **Supervisor agent:** Review the other agents' output and decide whether to approve or send work back.
- [x] **Decision graph:** Cyclic graph connecting Architect → Inventory → Solver → Supervisor.

### Epic 3.5: Symbolic deck object (Stage 1)

- [x] **DeckState:** Commander + 99 slots, identity, owned-only, complete-deck flag, budget cap, per-card price cap.
- [x] **Expanded validator:** Size, commander eligibility, Commander legalities/ban list, singleton, identity, *P*<sub>max</sub>, deck budget *B*. Owned copies cost 0 toward *B*.
- [x] **Price snapshot:** Scryfall `prices.usd` / `prices.eur` stored on `cards`, timestamp in `catalog_meta`. Re-run `scryfall_download.py` to refresh.
- [x] **JSON tools + deltas:** Architect proposes add/remove/**substitute** JSON; the graph applies it; supervisor **gate** is deterministic (`APPROVED` / `REJECTED`), LLM only explains.
- [x] **Task intents:** `build`, `improve`, `substitute`, `cut`. Upgrading or swapping cards on an existing list is valid; a full 99 is only required when the user asks to build a complete deck.
- [x] **99-card cap:** the committed list cannot exceed 99. Extra Architect picks go to `candidate_pool` for a later fill/cut, not into the deck.

### Epic 3.6: Combinatorial solver (Stage 2)

- [x] **Fill:** greedy select into remaining slots from `candidate_pool` (+ optional RAG), under identity, singleton, legality, *P*<sub>max</sub>, and *B*. Basics fill holes.
- [x] **Cut:** drop over-budget / over-99 cards; swap weak 99 slots for better pool cards using text synergy, role quotas, and redundancy. Committed list stays ≤ 99.
- [x] **Graph node:** Architect → Inventory → **Solver** → Supervisor.

### Epic 3.7: Geometry score (Stage 3 v1)

- [x] **Identity filter at query:** color bits on Chroma metadata (`--metadata-only`).
- [x] **Fill synergy:** cosine between commander and card embeddings when the index is loaded; tribal gate still blocks off-type creatures.
- [x] **Multi-view score:** oracle 0.7 + type 0.3 from `data/card_views.npz` (built once). Fill does not encode.
- [x] Keyword + mana-cost views (Stage 3.5e). Rebuild `--views-only` after `scryfall_download` so `keywords` exist.
- [ ] Second encoder (E5/BGE) for ablation — after Stage 3.5a–d, not before.

### Epic 3.8: Symbolic intelligence (Stage 3.5) — before TDA

Legal 99 is not enough: fill still uses CMC as a single number, a crude curve cap, and substring roles. The Architect searches without a diagnosis. Do this **before** topology. Details: [RESEARCH.md](RESEARCH.md) Stage 3.5.

- [x] **Mana algebra** (`src/mana.py`): parse costs and produced mana; deck pip vs source report. Soft score, not an LLM count.
- [x] **Plan-aware curve** in fill/cut: `fast` / `mid` / `high` from commander + ramp/cheat density. Soft `shape` term.
- [x] **Roles from keywords** (Scryfall) plus oracle classes: token producer vs token payoff. Search filters: `cmc_min` / `cmc_max` / `role`.
- [x] **Tools:** `diagnose_deck_json`, `score_card_json`. Diagnosis is injected into the Architect. `search_cards` accepts `cmc_min` / `cmc_max` / `role`.
- [x] **Prompts consume `deficits`.** Gap-shaped search queries. The model does not emit a 99 or compute pips.
- [x] **Embedding views:** keywords + mana-cost string in `card_views.npz` (offline). Rebuild with `python src/vectorize_cards.py --views-only`.

### Epic 4: Interface and Usability (Local Deploy)

After Stage 3.5. Charts display the diagnosis; they do not replace it.

- [ ] **Interactive CLI or Streamlit:** Basic Python UI to chat with the agents.
- [ ] **Mana curve and stats charts:** Plot the Stage 3.5 report (`matplotlib` or Streamlit).
- [ ] **Decklist export:** Produce a file ready to import back into Moxfield.

### Epic 5: Advanced Features (Research and Optimization)

After Stage 3.5. Topology is a solver prior, not a notebook.

- [ ] **Topological synergy analysis (TDA):** Islands and dense clusters **change which 99 cards are picked** (Stage 4).
- [x] **Cut algorithm (v1 greedy):** Drop worst slots by text redundancy, role quotas, curve, and synergy-per-dollar. TDA-informed cut comes in Stage 4.

---

## Usage Guide and Architecture

**ManaGraph** is a local Magic: The Gathering deckbuilding and semantic search system, built as a modular data-oriented (RAG) architecture with multi-agent orchestration.

Research stages (do not skip 3.5 for TDA): **1 DeckState → 2 fill/cut → 3 geometry → 3.5 mana/curve/tools/prompts/views → 4 TDA → 5 epidemiology → 6 UI.**

### Directory Structure

- `data/`: Local stores (`managraph.db`, `chroma_db/`, `card_views.npz`). **Not in git** — GitHub’s 100 MB file limit cannot hold ~38k MiniLM vectors. Rebuild with `python src/build_dataset.py`.
- `notebooks/`: Exploratory analysis notebooks (e.g. UMAP + Plotly dimensionality reduction).
- `src/`: Main source code:
  - `build_dataset.py`: One-shot catalog + Chroma + metadata + multi-view embeddings.
  - `scryfall_download.py`: Import and parse official Scryfall data, including a frozen price snapshot.
  - `catalog.py`: Oracle lookups, schema migration, acquisition cost, mana curve.
  - `deck_state.py`: First-class Commander deck object and JSON delta apply.
  - `roles.py`: Heuristic card roles (land, ramp, draw, interaction, threat).
  - `mana.py`: *(Stage 3.5)* pip/source algebra and deck mana report.
  - `geometry.py`: Cosine, identity `where` clauses, kNN in embedding space.
  - `solver.py`: Greedy fill/cut under symbolic constraints.
  - `import_inventory.py`: Load a physical card collection.
  - `inventory.py`: Check availability and move cards between the free pool and decks.
  - `embeddings.py`: Strategy pattern for embedding providers (model-agnostic).
  - `vectorize_cards.py`: Batch indexing into ChromaDB.
  - `data_collection.py`: Offline-first source adapters, normalization,
    provenance, and normalized external deck/recommendation persistence.
  - `hybrid_search.py`: Hybrid retrieve — MiniLM over type+oracle documents (card **name is metadata only**) plus SQLite lexical oracle match, then identity / price / role filters.
  - `retrieval_text.py`: Shared document format, junk filters, lexical phrase expansion, merge ranking.
  - `contracts.py`: Versioned, strict contracts between the LLM manager and deterministic core.
  - `manager_core.py`: Atomic, revision-checked plan application; catalog facts are never trusted from model output.
  - `symbolic_cards.py`: Deterministic card capability predicates and query requirement families.
  - `rules_validator.py`: Deterministic Commander legality, size, identity, singleton, budget.
  - `llm_factory.py`: Swap LLM providers via environment variables.
  - `tools.py`: LangChain `@tool` wrappers; tools return JSON.
  - `architect_agent.py`: Architect agent (synergy search, JSON deltas).
  - `inventory_agent.py`: Inventory manager agent.
  - `supervisor_agent.py`: Deterministic gate plus optional LLM explanation.
  - `main_agent.py`: LangGraph execution graph and demo entry point.
- `tests/`: Stage 1–3.5 unit tests (no LLM, no Chroma), including class invariants.

---

### How to Run

1. **Environment:** Fill in the `.env` file at the project root with your API key and provider (e.g. Google Gemini):

```env
LLM_PROVIDER=google
LLM_MODEL=gemini-2.5-flash
GOOGLE_API_KEY=your_key_here
```

2. **Dependencies:**

```bash
pip install -r requirements.txt
```

3. **Build the dataset** (needed once, and again when you want a new price snapshot or embeddings):

```bash
python src/build_dataset.py
```

This runs, in order: Scryfall download → Chroma oracle index → metadata stamp (color-identity bits, cmc) → multi-view file `data/card_views.npz` (oracle, type, keywords, mana) → test inventory if the collection is empty.

MiniLM encoding uses **CUDA** when PyTorch sees a GPU (batch size 256). If `torch.cuda.is_available()` is false, it falls back to CPU and prints a warning.

Useful flags: `--rebuild` (drop and re-encode Chroma), `--skip-download` (reuse SQLite), `--skip-views`, `--skip-inventory`.

### Coleta de fontes externas

O coletor mantém a tabela legada `cards` e adiciona tabelas normalizadas
`sources`, `datasets`, `external_decks`, `deck_cards`, `recommendations`,
`cooccurrence` e `provenance`. A ingestão é transacional e reexecutável;
nomes crus ficam armazenados para auditoria e IDs do catálogo são usados
quando resolvíveis.

No EDHREC, o formato público `container.json_dict.cardlists[].cardviews[]`
é normalizado sem scraping de HTML. Cada recomendação preserva categoria/tag,
posição, quantidade de inclusões (`inclusion_count`), universo
(`potential_decks`), percentual (`inclusion_percent`, de 0 a 100), sinergia,
salt e metadados no SQLite; o JSON original permanece em `raw_json`. Em uma
página de comandante, cada comandante→carta também vira uma aresta
`commander_recommendation` em `cooccurrence`, permitindo consultas de
relações carta-carta mesmo quando a página não fornece pares explícitos.
As colunas novas são uma migração aditiva (versão de esquema 3): bancos
existentes não são apagados nem reconstruídos.

```bash
# Oracle/Scryfall oficial (cache local + SQLite)
python scripts/collect_data.py --source scryfall

# Offline: JSONL ou JSONL.GZ exportado do bulk
python scripts/collect_data.py --source scryfall --fixture path/to/oracle_cards.jsonl.gz

# Fixtures JSON de deck/recomendações; --chroma cria coleções adicionais
python scripts/collect_data.py --source moxfield --fixture fixtures/moxfield.json --chroma
python scripts/collect_data.py --source edhrec --fixture fixtures/edhrec.json --chroma

# Sem URL/deck: descobre listagens públicas, pagina e busca detalhes
python scripts/collect_data.py --source moxfield --chroma
python scripts/collect_data.py --source edhrec --chroma

# Uma URL pública explicitamente autorizada pelo operador (modo compatível)
python scripts/collect_data.py --source edhrec --url https://example.invalid/export.json
```

Opções úteis incluem `--db-path`, `--chroma-path`, `--cache-dir`, `--dataset-id`,
`--sha256`,
`--limit`, `--max-pages`, `--page-size`, `--max-commanders`, `--dry-run`,
`--rebuild`, `--timeout`, `--retries`, `--rate-limit`, `--user-agent`,
`--no-details`, `--no-commander-pages` e `--no-robots` (somente quando houver
permissão explícita). Sem entrada, Moxfield usa a listagem pública paginada e
o endpoint público de detalhes; EDHREC usa suas páginas JSON públicas gerais e
segue os comandantes descobertos até o limite configurado. Todos os endpoints
podem ser substituídos por `--search-url`, `--deck-url-template`,
`--deck-url-fallback-template`, `--listing-url`, `--top-url-template` e
`--commander-url-template`. Em coleta pública EDHREC, `--limit` limita os
comandantes detalhados (e `--max-commanders` define o padrão); `--max-pages`
limita as páginas gerais.

No Moxfield, o resumo usa `publicId` (ou o ID extraído de `publicUrl`) — não o
`id` interno do resultado de busca. O detalhe tenta
`/v3/decks/all/{publicId}` e, somente para uma resposta HTTP 404 explícita,
`/v2/decks/all/{publicId}`. O fallback pode ser desligado quando um template
autorizado próprio for usado; respostas 401/403 nunca acionam outro endpoint.
Se a busca não trouxer `publicId` nem `publicUrl`, o resumo é preservado e
nenhum detalhe é solicitado com o `id` interno. O resultado informa
`summary_count`, `summaries_without_public_id`, `detail_successes` e
`detail_fallback_attempts`, para distinguir resumos válidos de decklists
enriquecidas ou falhas de detalhe.

Na verificação pública de 28/08/2026, um `GET` sem credenciais para
`/v2/decks/all/{publicId}` retornou JSON com `publicId`, `commanders` e
`mainboard`; esse é o fundamento do fallback v2. Isso não constitui uma API
oficial nem garante disponibilidade futura: o v3 observado na coleta retornou
404 e endpoints não documentados podem mudar.

Moxfield e EDHREC não têm API pública oficial. Os endpoints Moxfield acima são
interfaces não documentadas usadas por clientes públicos e podem mudar; a
documentação comunitária que motivou o fallback é a
[nota de design do moxtags](https://github.com/natefinch/moxtags/blob/main/DESIGN.md).
O JSON do EDHREC (`json.edhrec.com`) também não é uma API/export autorizado:
referências comunitárias apenas descrevem arquivos estáticos usados pelo site.
Não há fallback para HTML, `__NEXT_DATA__`, proxy, credencial ou automação de
navegador. Um 401/403 é uma falha terminal clara; use um export autorizado por
`--fixture`/`--file` ou um `--listing-url` explicitamente fornecido pelo
operador com permissão.

O coletor consulta robots.txt, respeita intervalo entre requisições, faz
retries limitados para 429/5xx, usa cache por URL/hash e guarda
tamanho/timestamp/proveniência. Consulte o
[FAQ do EDHREC](https://edhrec.com/faq) e os
[Termos do EDHREC](https://edhrec.com/terms) antes de configurar qualquer
coleta. Dados de recomendação são sugestões de origem; legalidade para
montagem continua sendo validada pelo catálogo/`rules_validator`, não por
EDHREC ou Moxfield.

After changing the embedding document format (e.g. dropping card names from Chroma text), rebuild the index:

```bash
python src/vectorize_cards.py --rebuild
```

`build_dataset.py --rebuild` does the same Chroma step as part of the full pipeline.

The LLM is a planning manager only. It proposes typed operations; the Manager
resolves cards through the catalog, applies plans atomically, runs the
deterministic Solver/CommanderValidator, and owns the final gate. Inventory
agents have read-only tools; physical allocation requires an explicit
confirmation command.

4. **Tests** (validator, deltas, fill/cut, geometry; no API key):

```bash
python -m unittest discover -s tests
```

Run the deterministic full-deck selection benchmark:

```bash
python scripts/eval_selection.py
```

5. **Run the multi-agent loop** (JSON delta → inventory → fill/cut → symbolic supervisor):

```bash
python src/main_agent.py --commander "Ertai Resurrected"
```

Phrases like `build me a deck`, `full deck`, or `99 cards` tell the solver to fill to 99. `--owned-only` prefers cards in your inventory. You can also pass a free-text request:

```bash
python src/main_agent.py "Build me a full Dimir control deck for Ertai Resurrected with counters and removal."
```
