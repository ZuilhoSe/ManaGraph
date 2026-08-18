## Development Roadmap

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
- [x] **Decision graph:** Cyclic graph connecting Architect → Inventory → Supervisor.

### Epic 4: Interface and Usability (Local Deploy)

Make the system usable without running everything from a terminal while physically assembling decks.

- [ ] **Interactive CLI or Streamlit:** Basic Python UI to chat with the agents.
- [ ] **Mana curve and stats:** Simple charts (`matplotlib` or native Streamlit) for the deck output.
- [ ] **Decklist export:** Produce a file ready to import back into Moxfield.

### Epic 5: Advanced Features (Research and Optimization)

Stretch goals for denser mathematical deckbuilding.

- [ ] **Topological synergy analysis (TDA):** Topological metrics on the embedding space to find isolated "islands" (cards with no synergy) or redundancy.
- [ ] **Cut algorithm:** Given a list of 110 cards, mathematically pick the 10 worst slots using vector redundancy and mana-curve weight.

---

## Usage Guide and Architecture

**ManaGraph** is a local Magic: The Gathering deckbuilding and semantic search system, built as a modular data-oriented (RAG) architecture with multi-agent orchestration.

### Directory Structure

- `data/`: Local stores (`managraph.db` for SQLite inventory and structured rules, `chroma_db/` for the vector index).
- `notebooks/`: Exploratory analysis notebooks (e.g. UMAP + Plotly dimensionality reduction).
- `src/`: Main source code:
  - `scryfall_download.py`: Import and parse official Scryfall data.
  - `import_inventory.py`: Load a physical card collection.
  - `inventory.py`: Check availability and move cards between the free pool and decks.
  - `embeddings.py`: Strategy pattern for embedding providers (model-agnostic).
  - `vectorize_cards.py`: Batch indexing into ChromaDB.
  - `hybrid_search.py`: Search engine combining vector similarity and SQLite filters.
  - `rules_validator.py`: Deterministic Commander color identity and singleton checks.
  - `llm_factory.py`: Swap LLM providers via environment variables.
  - `tools.py`: LangChain `@tool` wrappers for the agents.
  - `architect_agent.py`: Architect agent (synergy search).
  - `inventory_agent.py`: Inventory manager agent.
  - `supervisor_agent.py`: Supervisor that approves or rejects proposals.
  - `main_agent.py`: LangGraph execution graph and demo entry point.

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

3. **Vector index** (reindex Magic cards from the relational database if needed):

```bash
python src/vectorize_cards.py
```

4. **Run the multi-agent loop** (synergy search + inventory + supervisor):

```bash
python src/main_agent.py
```
