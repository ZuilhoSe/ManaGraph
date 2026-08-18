# Research roadmap: neuro-symbolic Commander deck building

ManaGraph should become a **neuro-symbolic agentic solver** for Commander deck building (a constrained combinatorial problem), with a path to publication. This note is the research contract: what exists, what the scientific claim is, how the author's profile maps onto contributions, and which stages to do in which order.

Epics 1–3 in the README are real as **infrastructure**, not as a research system. Close that gap before a UI.

---

## What the repo actually is today

A **neuro-symbolic solver** with a RAG front-end. Stages 1–3 exist; the remaining gap before topology is **symbolic deck intelligence**, not a UI.

| Layer | What exists | Gap before topology (Stage 3.5) |
|---|---|---|
| Symbolic | `DeckState`, 99, legality, budget, role quotas, CMC histogram | pip vs source algebra, target curve, keyword roles, a diagnosis object the solver consumes |
| Neural | MiniLM concat retrieval; oracle+type views for score (`card_views.npz`) | keyword + mana-cost views; E5/BGE only as an ablation |
| Agentic | Architect → Inventory → Solver → Supervisor | Architect has search but no diagnosis; prompts do not consume deficits |
| Evaluation | unittest stages 1–3 | MiniLM vs multi-view vs symbolic-terms ablation on 3–5 commanders |

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

MiniLM-on-oracle-text is a **baseline**, not a contribution. A paper needs at least: concat MiniLM vs multi-view (oracle + type + keywords + mana-cost string) vs the same views **plus symbolic terms** (curve, pips, roles), then a TDA-informed cut. Do the first two comparisons in Stage 3.5, before Mapper.

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
- [x] **Multi-view score** (no encode at fill): oracle 0.7 + type line 0.3 from `data/card_views.npz`. Name is not a view. Build once with `python src/vectorize_cards.py --views-only`.
- [ ] **Observation graph**: optional EDHREC/Moxfield co-occurrence, stored separately — do not add into the fill score
- [ ] Stronger encoder (E5/BGE) as a second ablation condition

Retrieval still uses the concatenated MiniLM index. Scoring looks up oracle/type views from `card_views.npz` (no MiniLM at fill). Do **not** jump to TDA or E5 yet: Stage 3.5 has to make mana, curve, roles, tools, and prompts first-class, and add the remaining embedding views.

### Stage 3.5 — Symbolic intelligence, tools, prompts, embedding views

**Status: 3.5a–d v1 in code.** Do 3.5e (keyword/mana views) before Stage 4.

Stages 1–3 produce a legal 99 with a geometry score. Fill still treats mana as a CMC integer, curve as “this CMC bucket already has ≥ 18 cards,” roles as oracle substrings, and the Architect as a search chatbot that never sees a diagnosis. Topology on that scorer would measure a weak object.

**Claim:** more of Commander construction is **symbolic** than neural. Embeddings retrieve candidates. Algebra and quotas decide whether the 99 can be cast and whether slots have the right shape. The LLM names intents and explains the report — it does not count Mountains, pips, or the curve.

If those numbers live only in the prompt, this stage failed.

#### Already in the repo (do not rebuild)

- CMC histogram on `enrich_deck` / solver context (lands excluded)
- Curve penalty if a non-land CMC bucket has ≥ 18 cards
- Role quotas (land / ramp / draw / interaction / threat) via oracle phrases in `roles.py`
- `mana_cost` stored on `cards`, unused except display
- Architect tools: `search_cards`, `list_inventory_cards` (validate/fill/cut exist but are not on the Architect)
- Supervisor: deterministic legality gate; LLM explains

#### 3.5a Mana algebra (symbolic, no MiniLM)

New module `src/mana.py`, pure functions, unit-tested:

- Parse `{2}{R}{R}`, hybrid pips, `{X}`, Phyrexian into generic + colored counts
- Parse produced mana from oracle (`add {R}`, “any color”, treasure, “add one mana”) — imperfect regex is fine if tests pin known cards
- Deck report: colored pip intensity vs colored sources (lands + rocks), generic density, average CMC excluding lands, land count vs quota, fast mana (CMC 0–1 non-land sources)
- Identity leak is already a hard error; this layer is **castability and shape**, not legality

v1: mana is a **soft objective + diagnosis** (same status as roles). Do not reject a 99 for “uncastable commander” until the parser is trusted.

Keep pip counts as **numbers the solver adds**. Do not put prices in embeddings. Do not make MiniLM the only representation of `{2}{R}`.

#### 3.5b Curve and roles as solver terms

- Target curve **depends on plan**: `fast` (token/combat, little ramp), `mid`, or `high` (lots of ramp or cheat-into-play). A low curve is not always the goal; 7-drops are fine when the 99 can actually cast or sneak them.
- Land count: missing lands are a fill priority, not only a role bonus
- Role classifier: Scryfall `keywords` + type line first, oracle phrases as fallback
- Split token **producer** vs token **payoff** when the commander cares (Krenko: make Goblins / haste / sacrifice)

`solver._score_parts` must consume `mana_report` and `curve_gap`. Charts in Streamlit are Stage 6; they are not this work.

#### 3.5c Tools (JSON)

| Tool | Returns |
|---|---|
| `diagnose_deck_json` | curve, avg CMC, land count, roles vs quotas, pip vs source, budget slack, remaining slots, named deficits |
| `score_card_json` | `score_breakdown` plus mana/curve terms |
| `search_cards` | optional `cmc_min` / `cmc_max` / `role` (identity and *P*<sub>max</sub> already exist) |

The Architect must **see a diagnosis** before improve/cut deltas (injected into the user message, or a mandatory tool call). Do not add a tool whose job is “ask the LLM to count the curve.”

#### 3.5d Prompting

Prompts **consume** the diagnosis; they do not invent it.

- Inject a short `deficits` block into the Architect turn
- Search queries are gap-shaped: `"2-mana goblin"`, `"add {R}"`, not `"good Krenko cards"`
- Supervisor may summarize curve/mana **warnings** from the report; it still does not override the gate
- Forbidden: the model emitting a 99, computing pips, or assigning a synergy number

#### 3.5e Embedding views (offline, no encode at fill)

Finish “embeddings worth publishing” without training a new net and without E5 as a blocker:

- Persist Scryfall `keywords` on `cards` at download time
- Add views to `card_views.npz`: **keywords** (joined list), **mana-cost string** (`{2}{R}`). Oracle stays the largest weight; keywords and mana smaller than type. Name stays out.
- Retrieval: MiniLM concat for recall; optional BM25 over oracle as a second candidate source (hybrid sparse+dense)
- Rebuild views once (`--views-only`). Fill still only looks up ids
- E5/BGE is an ablation **after** 3.5a–d exist, so there is a task metric (legal 99 + role coverage + mana/curve gaps)

Ablation for the paper skeleton: concat MiniLM vs oracle+type vs oracle+type+keywords+mana, **with the same symbolic terms**, on 3–5 commanders.

#### Order of work

- [x] `mana.py` + tests on printed costs and a Krenko 99 pip/source report
- [x] Target curve + mana terms in `solver._score_parts`
- [x] `diagnose_deck_json` + inject diagnosis into the Architect
- [ ] Keywords column + extra views in `--views-only`
- [ ] Ablation rows in the analysis notebook

Then Stage 4 TDA, on a scorer that already knows curve and mana.

### Stage 4 — Topology as a solver prior (signature section)

Only after Stages 1–3.5: a legal 99 **and** symbolic mana/curve/role diagnosis that the solver uses:

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
- **Shape (Stage 3.5):** land count vs quota, average CMC band, colored pips vs sources, named deficits from `diagnose_deck_json` (not from the LLM)
- **Geometric:** intra-deck cohesion, fraction of singleton islands (missing glue), persistence-barcode stability under swap
- **Novelty (primary for the discovery paper):** for each pick, geometric score vs inclusion rate. Success is high synergy + low popularity, not Jaccard with EDHREC
- **Contrast only:** overlap with EDHREC / a personal gold list, reported as “how meta is this?” — high overlap is a warning, not a win
- **Agent:** number of repair cycles until symbolic validity (should fall when the solver, not the LLM, owns constraints)

No LLM-as-judge as the **primary** metric. Do not maximize overlap with published decks.

---

## What not to do next

- Streamlit / charts first — they lock a retrieval chatbot, not a solver. A mana-curve **plot** is Stage 6; mana-curve **algebra** is Stage 3.5
- Skipping Stage 3.5 for TDA — homology on a scorer that ignores pips and target curve is a pretty diagram of a weak 99
- Asking the LLM to count lands, pips, or the curve — that is the solver’s job; the prompt only consumes `deficits`
- Putting prices (or pip counts as the sole signal) into MiniLM documents
- Training or scoring on EDHREC/Moxfield co-occurrence — that clones the meta
- Using staple overlap as the main evaluation — that punishes overlooked strategies
- Training a new embedding model before a task metric exists — extra **views** and E5 as an ablation are in 3.5e; a custom MTG encoder is not
- Claiming “multi-agent intelligence” as the result — the graph is orchestration; the result is the 99-card set + geometry
- One mega-paper that mixes NeSy, TDA, epidemiology, and a UI — split: **geometry of cards**, then **neuro-symbolic construction**, then **contagion on synergy graphs**

---

## Immediate milestone

**Legal 99 from a commander + intent, with a reproducible cut.**

Deliverables:

1. `DeckState` + expanded validator (size + legalities + **price caps**)
2. Structured tool I/O
3. Symbolic fill/cut (greedy is enough for v1; knapsack inequality for *B*)
4. Stage 3.5: mana algebra, target curve, diagnosis tool, Architect prompt that consumes deficits
5. MiniLM vs multi-view vs multi-view+keywords/mana ablation on 3–5 commanders (same symbolic terms)
6. A notebook that is actually in the repo (the README already promises UMAP; there is none)

That milestone is both a better product and the skeleton of a CoG/NeSy paper. TDA and epidemiology plug in without rewriting the agents.

**Default path:** Stage 3.5 (mana/curve/roles → tools → prompts → extra views) → Stage 4 TDA. Not Streamlit. Not E5 until 3.5a–d exist. Not epidemiology.
