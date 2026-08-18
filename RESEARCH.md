# Research roadmap: neuro-symbolic Commander deck building

ManaGraph should become a **neuro-symbolic agentic solver** for Commander deck building (a constrained combinatorial problem), with a path to publication. This note is the research contract: what exists, what the scientific claim is, how the author's profile maps onto contributions, and which stages to do in which order.

Epics 1–3 in the README are real as **infrastructure**, not as a research system. Close that gap before a UI.

---

## What the repo actually is today

A **neuro-adjacent RAG agent** over Scryfall + inventory:

| Layer | What exists | What is missing for NeSy / a paper |
|---|---|---|
| Symbolic | Color identity + singleton checks; inventory moves | 100-card size, legality/banned list, curve, land count, **prices**, a **deck object** |
| Neural | MiniLM on `name + type + oracle text`; Chroma cosine retrieval | MTG-aware embeddings, card–card geometry, hybrid retrieval that does not drop hits in post-filters |
| Agentic | Architect → Inventory → Supervisor (max 3 cycles) | Structured state (`selected_cards` is unused), hard constraints, a cut/fill optimizer |
| Evaluation | One hardcoded query in `main_agent.py` | Gold decks, metrics, ablations, baselines |

The scientific claim cannot be “LangGraph builds Commander decks.” Reviewers will treat that as a demo. The claim should be: **constrained subset selection in a heterogeneous embedding space, with topology (and optionally contagion) as structure, and an LLM only as a proposer/explainer.**

---

## Problem statement

Commander construction **and revision** is **subset selection with higher-order interactions**:

- Universe *C* (Oracle catalog), inventory *I* ⊆ *C*, commander with identity χ
- Choose *D* ⊂ *C*, |*D*| = 99, singleton, identity ⊆ χ, format-legal — **or** revise an existing *D* (add, cut, substitute) without requiring a from-scratch 99
- Budget (hard): per-card cap *p*<sub>c</sub> ≤ *P*<sub>max</sub>, deck cap ∑ *p*<sub>c</sub> ≤ *B*; owned copies may have *p*<sub>c</sub> = 0
- Soft objectives: synergy, curve, interaction density, owned-first, theme coherence — **not** “look like decks people already publish”
- Valid tasks: **build**, **improve** (better cards for weak slots), **substitute** (atomic out→in), **cut** (drop redundant slots)

Pairwise synergy already makes this combinatorial; combos make it **hypergraph** selection. LLMs cannot enumerate that space. The architecture should be:

1. **Neural** — retrieve and score candidates
2. **Symbolic** — enforce legality and fill/cut to 99
3. **Geometric** — topology of the embedding as a prior / regularizer
4. **LLM** — propose intents and explain, never as the source of truth for legality

That is neuro-symbolic. Today the supervisor is linguistic (`"APPROVED"` in a string). That is not reproducible science.

---

## Discovery vs the existing meta

Using EDHREC or Moxfield **as training data or as the objective** would make the system clone decks that already exist. Popularity is a record of what people played, not of what is combinatorially coherent, and not of what a constrained subset (budget, inventory, overlooked cards) can do.

Keep play-data corpora **out of the generator**. Use them as a **contrast set**:

| Role | Allowed? | Why |
|---|---|---|
| Train / fine-tune to predict “cards that co-occur on EDHREC” | **No** | Supervised cloning of the meta |
| Score a candidate by inclusion rate or salt score | **No** (as the objective) | Same cloning, just at inference time |
| Baseline to *compare against* after the fact | **Yes** | Shows staple overlap vs novel picks |
| Novelty filter: down-weight or exclude high-inclusion staples | **Yes** | Forces search onto the overlooked subset |
| Diagnostic: high geometric synergy, low observed co-occurrence | **Yes** | This is the discovery claim |

The LLM is a second popularity leak: it has seen primers, EDHREC, and Reddit. Geometric + symbolic scoring must be allowed to **overrule** the Architect when the proposal is only a staple list.

**Overlooked strategy (operational definition).** A cluster in embedding / Mapper space that is tightly connected by oracle-text geometry (and legal under χ), but weakly connected in the observed co-occurrence graph. Those nodes are the candidates most people do not use. The solver’s job is to cover that cluster, not to maximize Jaccard with the average list.

**Optimal on a subset nobody uses.** Restrict the universe *before* fill/cut:

- inventory-only (already in the product)
- rarity / date printed
- **price:** drop any card with *p*<sub>c</sub> > *P*<sub>max</sub>; keep a running sum so the 99 stay under *B*
- exclude top-*k*% inclusion-rate cards for that commander (anti-staple mode)
- restrict to a Mapper island that EDHREC barely occupies

Then “optimal” means: legal 99, geometrically coherent, role-complete, on that restricted *C*. It does not mean “would beat a cEDH table.” Win-rate without a simulator is not a claim this project should make.

Two graphs, never fused into one score by default:

1. **Geometry graph** — kNN / TDA on embeddings (text, type, keywords). Independent of play data.
2. **Observation graph** — EDHREC/Moxfield co-occurrence. Independent of the solver.

Discovery = geometry-strong, observation-weak. Redundancy = geometry-dense (cut). Meta-cloning = observation-strong, used as a loss.

This is also the epidemiology paper: the **geometric outbreak** (what *could* spread from the commander) vs the **observed outbreak** (what *did* spread). Islands that infect in geometry but not in the corpus are overlooked strategies.

---

## Budget as a hard constraint (not an LLM hint)

Price is part of the **symbolic** layer. The Architect must not “try to keep it cheap.” Scryfall already exposes `prices.usd` / `prices.eur` on oracle cards; the catalog importer currently drops them. Freeze a **price snapshot** next to the bulk date so experiments are reproducible (prices move daily).

Treat cost as a function of **acquisition**, not of list price of every name:

| Copy | Cost toward *B* |
|---|---|
| Already in inventory / allocated to this deck | 0 (or a small opportunity cost, if you later model competing decks) |
| Buy-list (not owned) | snapshot market price of the cheapest legal printing |
| Foil / specific printing | only if the user asked for that printing |

Two nested constraints, both deterministic:

1. **Per-card cap** *P*<sub>max</sub>: filter *C* *before* retrieval. This is a restricted universe (same move as inventory-only). Example: no card over USD 5.
2. **Deck cap** *B*: ∑<sub>c ∈ *D*</sub> *p*<sub>c</sub> ≤ *B*. This is a knapsack on top of the 99-card, identity, and role constraints. Greedy fill can respect it; ILP encodes it as one linear inequality.

Optional third mode, later: **Pareto front** of geometric score vs spend. Do not collapse that into one magic number until you have to. A figure of “synergy vs dollars” under a frozen snapshot is a paper figure; “the LLM said it was budget” is not.

Budget also **helps discovery**. Staples are expensive; a tight *P*<sub>max</sub> or *B* is an anti-meta prior without touching EDHREC. Evaluate that explicitly: same commander, unconstrained vs *P*<sub>max</sub> = USD 2 vs *B* = USD 50, report novelty and role coverage — not whether the cheap deck looks like the EDHREC average.

Do **not** put prices in the embedding. Cost is not semantics. Keep *p*<sub>c</sub> as metadata on the card row and as an ILP coefficient.

When Stage 1 lands, `DeckState` should carry `budget_used`, `budget_cap`, `max_card_price`, and `unowned_cost` so the supervisor can reject a delta that breaks *B* the same way it rejects a color-identity leak.

---

## How the academic profile becomes the contribution

Do not add TDA, epidemiology, and UMAP as features. Pick **one backbone claim** and let the others be methods.

### 1. Topology of embeddings (strongest near-term paper)

Oracle-text MiniLM is a point cloud. Persistent homology / Mapper can show:

- **islands** = disconnected strategies (tokens vs spellslinger vs stax)
- **dense clusters** = redundancy (the cards the cut algorithm should drop)
- **persistence diagrams as deck signatures**, compared with Wasserstein distance

This matches topology for embeddings and projections, and it can run **before** the agent is good.

### 2. Epidemiology on the synergy graph (distinctive, later)

Build a directed graph: commander seeds, “enables” edges (text, co-occurrence, embedding kNN). Then:

- theme as an **outbreak**; *R*<sub>eff</sub> of an engine
- tutors / token outlets as **super-spreaders**
- the cut algorithm as **intervention** (remove redundant infecteds)

That is a real second paper (complex networks / computational social science venues), not a README bullet.

### 3. Projections as method, not as a notebook

UMAP/Mapper should **define neighborhoods for the solver** (candidate pools, redundancy sets), not only pretty plots. The trajectory of fill/cut in the projected space is a figure reviewers understand.

MiniLM-on-oracle-text is a **baseline**, not a contribution. A paper needs at least: oracle-only vs multi-view (text + type + keywords + mana) vs a graph/TDA-informed embedding, with an ablation.

---

## Recommended sequence

### Stage 0 — Research contract (1 week, no new features)

Write a short note (this file) and freeze it as the paper intro:

- formal problem, constraints, objective
- what is neural / symbolic / topological
- what we will **not** claim (win rate on MTGO, “AGI deckbuilder”)
- target venues: **IEEE CoG / AIIDE** for the system; **NeSy workshop** for the architecture; **complex networks / TDA** for the geometry-contagion papers. A journal article later, after metrics exist.

Also freeze a **data snapshot** (Scryfall bulk date, **price snapshot**, embedding model, inventory). Papers die on irreproducible catalogs and on live market APIs.

### Stage 1 — Deck as a first-class symbolic object (2–3 weeks)

**Status: implemented in code.** Agents chat less; the graph mutates a `DeckState` and a deterministic gate owns legality.

- [x] `DeckState`: commander, 99 slots, curve histogram, identity, owned vs wishlist, budget used vs cap, per-card price cap
- [x] Validator: size, commander legality, banned list (`legalities`), singleton, identity, basic-land exceptions, *p*<sub>c</sub> ≤ *P*<sub>max</sub>, ∑ *p*<sub>c</sub> ≤ *B*
- [x] Catalog: persist Scryfall `prices` (usd/eur) on `cards`; owned copies cost 0 toward *B* unless the user is costing a buy-from-scratch list
- [x] Tools return **JSON**, not prose; Supervisor is a **deterministic gate** (valid / invalid + reason), with the LLM only explaining
- [x] Architect proposes **deltas** (add / remove / **substitute**), Inventory applies them, Supervisor checks invariants
- [x] Task intents: `build`, `improve`, `substitute`, `cut`. A swap or upgrade is a complete job; do not require a new 99 unless the user asked to build a full deck
- [x] Hard cap: committed main deck never exceeds 99. Architect overflow goes to `candidate_pool` for later fill/cut, not into the 99

Without this, Epic 4 (Streamlit) will freeze a toy.

### Stage 2 — Combinatorial core: fill and cut (3–4 weeks)

**Status: v1 greedy implemented.** ILP / embedding distances can replace the scorer later without changing `DeckState`.

**Fill:** from a commander + intent, retrieve a candidate pool *P* (|*P*| ≈ 150–300), merge with `DeckState.candidate_pool` (Architect overflow, never part of the 99), then select 99 by beam search or ILP (PuLP/OR-Tools) with hard constraints (including *P*<sub>max</sub> and *B*) and a linear synergy score.

**Cut:** given the 99 plus pool extras, drop the worst by redundancy (embedding nearest-neighbor density) + curve penalty + role quotas. If over budget, cut the worst synergy-per-dollar (or the most redundant expensive card) until ∑ *p*<sub>c</sub> ≤ *B*. The committed list stays ≤ 99 at every step.

Baselines (none of these is “match EDHREC”):

1. random legal 99
2. top-*k* cosine retrieval (geometry only)
3. EDHREC average list — **contrast**, not a target: report staple overlap *and* novel picks
4. the agent **without** the symbolic optimizer
5. anti-staple ablation: same solver after dropping high-inclusion cards, to show it still builds a legal coherent 99
6. budget ablation: unconstrained vs *P*<sub>max</sub> vs deck cap *B* — legality and role coverage must still hold; spend must respect the snapshot

### Stage 3 — Embeddings worth publishing (parallel with Stage 2)

**Status: v1 in code.** MiniLM documents unchanged; the solver now scores **commander↔card cosine** from the existing index (not query-to-name distance). Hybrid search can filter identity **at query time** once metadata is stamped.

- [x] **Metadata in the index** (cmc, color-identity bits, is_creature) — `python src/vectorize_cards.py --metadata-only`
- [x] **Geometry score**: fill/cut use cosine(commander embedding, card embedding) when Chroma is loaded; Jaccard is the fallback
- [x] **kNN helper** (`geometry.knn_indices`) for later TDA / redundancy
- [ ] **Multi-view embeddings**: encode oracle / type / name separately, then fuse
- [ ] **Observation graph**: optional EDHREC/Moxfield co-occurrence, stored separately — do not add into the fill score
- [ ] Stronger encoder (E5/BGE) as a second ablation condition

Current index still concatenates name + type + oracle into MiniLM. Next: fuse views without using the commander *name* as a retrieval query.

### Stage 4 — Topology as a solver prior (signature section)

Only after Stages 1–3 have a legal 99-card output:

- persistence on the candidate cloud (H0 islands = missing glue cards; dense clusters = cut targets)
- Mapper graph as a **strategy skeleton** the Architect must cover (at least one card per relevant node)
- deck-to-deck distance via persistence diagrams

This upgrades Epic 5 from “nice TDA plots” to “topology changes which 99 cards you pick.” That is the sentence that belongs in an abstract.

### Stage 5 — Epidemiology layer (second paper, not a blocker)

Once the **geometry** graph exists: seed from commander, run a discrete contagion, compare that infected set to the **observed** outbreak on EDHREC/Moxfield. Treat cut as vaccination of redundant nodes. Cards that infect geometrically but not in the corpus are the overlooked set. Do not start here; the geometry graph and a scoring function come first, and they must not be trained on the observation graph.

### Stage 6 — Interface (README Epic 4), after the object model

CLI or Streamlit that shows: proposed 99, legality report, curve, UMAP, which cards were cut and why. Export to Moxfield. Usability is for a demo, not the contribution.

---

## Evaluation

Start a stub in Stage 1, or the paper never happens. Freeze a small test suite:

- **Hard:** legal 99, identity, singleton, owned-only / restricted-universe when requested, **p<sub>c</sub> ≤ P<sub>max</sub>** and **∑ p<sub>c</sub> ≤ B** on a frozen price snapshot
- **Budget quality:** leftover slack *B* − ∑ *p*<sub>c</sub> is not a goal; violating *B* is a fail. Optional later: Pareto of geometric score vs spend
- **Role coverage:** ramp, draw, interaction, wincon, lands — independent of the meta
- **Geometric:** intra-deck cohesion, fraction of singleton islands (missing glue), persistence-barcode stability under swap
- **Novelty (primary for the discovery paper):** for each pick, geometric score vs inclusion rate. Success is high synergy + low popularity, not Jaccard with EDHREC
- **Contrast only:** overlap with EDHREC / a personal gold list, reported as “how meta is this?” — high overlap is a warning, not a win
- **Agent:** number of repair cycles until symbolic validity (should fall when the solver, not the LLM, owns constraints)

No LLM-as-judge as the **primary** metric. Do not maximize overlap with published decks.

---

## What not to do next

- Streamlit / charts first — they lock a retrieval chatbot, not a solver
- Training or scoring on EDHREC/Moxfield co-occurrence — that clones the meta
- Using staple overlap as the main evaluation — that punishes overlooked strategies
- Training a new embedding model before a task metric exists
- Claiming “multi-agent intelligence” as the result — the graph is orchestration; the result is the 99-card set + geometry
- One mega-paper that mixes NeSy, TDA, epidemiology, and a UI — split: **geometry of cards**, then **neuro-symbolic construction**, then **contagion on synergy graphs**

---

## Immediate milestone

**Legal 99 from a commander + intent, with a reproducible cut.**

Deliverables:

1. `DeckState` + expanded validator (size + legalities + **price caps**)
2. Structured tool I/O
3. Symbolic fill/cut (greedy is enough for v1; knapsack inequality for *B*)
4. MiniLM vs multi-view ablation on 3–5 commanders
5. A notebook that is actually in the repo (the README already promises UMAP; there is none)

That milestone is both a better product and the skeleton of a CoG/NeSy paper. TDA and epidemiology plug in without rewriting the agents.

**Default path:** Stage 2 greedy fill/cut is in the graph. Stage 3 v1 is metadata-at-query + commander↔card cosine. Next is **multi-view fusion**, then Stage 4 TDA.
