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
- [ ] Second encoder (E5/BGE) for ablation.

### Epic 4: Interface and Usability (Local Deploy)

Make the system usable without running everything from a terminal while physically assembling decks.

- [ ] **Interactive CLI or Streamlit:** Basic Python UI to chat with the agents.
- [ ] **Mana curve and stats:** Simple charts (`matplotlib` or native Streamlit) for the deck output.
- [ ] **Decklist export:** Produce a file ready to import back into Moxfield.

### Epic 5: Advanced Features (Research and Optimization)

Stretch goals for denser mathematical deckbuilding.

- [ ] **Topological synergy analysis (TDA):** Topological metrics on the embedding space to find isolated "islands" (cards with no synergy) or redundancy.
- [x] **Cut algorithm (v1 greedy):** Drop worst slots by text redundancy, role quotas, curve, and synergy-per-dollar. TDA-informed cut comes in Stage 4.

---

## Usage Guide and Architecture

**ManaGraph** is a local Magic: The Gathering deckbuilding and semantic search system, built as a modular data-oriented (RAG) architecture with multi-agent orchestration.

### Directory Structure

- `data/`: Local stores (`managraph.db`, `chroma_db/`). **Not in git** — GitHub’s 100 MB file limit cannot hold ~38k MiniLM vectors. Rebuild with `scryfall_download.py` then `vectorize_cards.py`.
- `notebooks/`: Exploratory analysis notebooks (e.g. UMAP + Plotly dimensionality reduction).
- `src/`: Main source code:
  - `scryfall_download.py`: Import and parse official Scryfall data, including a frozen price snapshot.
  - `catalog.py`: Oracle lookups, schema migration, acquisition cost, mana curve.
  - `deck_state.py`: First-class Commander deck object and JSON delta apply.
  - `roles.py`: Heuristic card roles (land, ramp, draw, interaction, threat).
  - `geometry.py`: Cosine, identity `where` clauses, kNN in embedding space.
  - `solver.py`: Greedy fill/cut under symbolic constraints.
  - `import_inventory.py`: Load a physical card collection.
  - `inventory.py`: Check availability and move cards between the free pool and decks.
  - `embeddings.py`: Strategy pattern for embedding providers (model-agnostic).
  - `vectorize_cards.py`: Batch indexing into ChromaDB.
  - `hybrid_search.py`: Search engine combining vector similarity and SQLite filters (including *P*<sub>max</sub>).
  - `rules_validator.py`: Deterministic Commander legality, size, identity, singleton, budget.
  - `llm_factory.py`: Swap LLM providers via environment variables.
  - `tools.py`: LangChain `@tool` wrappers; tools return JSON.
  - `architect_agent.py`: Architect agent (synergy search, JSON deltas).
  - `inventory_agent.py`: Inventory manager agent.
  - `supervisor_agent.py`: Deterministic gate plus optional LLM explanation.
  - `main_agent.py`: LangGraph execution graph and demo entry point.
- `tests/`: Stage 1–2 unit tests (no LLM, no Chroma).

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

3. **Catalog + prices** (needed once, and again when you want a new price snapshot):

```bash
python src/scryfall_download.py
python src/vectorize_cards.py
python src/vectorize_cards.py --metadata-only
python src/vectorize_cards.py --views-only
```

`--metadata-only` stamps color-identity bits on an existing Chroma index (no MiniLM). Needed once after upgrading, so hybrid search can filter identity at query time.

4. **Tests** (validator, deltas, fill/cut, geometry; no API key):

```bash
python -m unittest tests.test_stage1 tests.test_stage2 tests.test_stage3
```

5. **Run the multi-agent loop** (JSON delta → inventory → fill/cut → symbolic supervisor):

```bash
python src/main_agent.py
```
