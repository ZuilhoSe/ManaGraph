# Retrieval quality: what was tested, what shipped, what didn't work

Origin: a real run (Aminatou, Esper enchantments, 2026-08-20) surfaced 3 problems —
`cut()` undoing the Architect's own picks in the same round, `candidate_pool` retrieval
missing obviously-relevant cards, and the Architect hallucinating a card's mechanics in
its `reason` text. This note covers the investigation into the second one (retrieval),
plus what shipped for the first. All experiments below were run against a local
reindex/copy of the MiniLM embeddings — production `data/chroma_db` and the embedding
model itself were never modified.

## 1. Shipped in code

- **`cut()` no longer undoes cards the Architect just added/substituted in the same
  round.** `solver.py::_freshly_touched_names()` reads `deck.last_delta` and
  `_worst_cut()` skips those names as swap victims. Also fixed a silent bug in
  `deck_state.py::from_dict()` that dropped `last_delta` on every graph-node round-trip
  (which made the protection above a no-op even once written, and also fixed a latent
  bug in `InventoryAgent`'s prompt, which reads the same field). Tests:
  `tests/test_stage2.py`.
- **`detect_known_themes(cmd)`** (`solver.py`): keyword match on the commander's
  `oracle_text` ("enchantment"/"artifact"/"graveyard") triggers extra retrieval queries
  from `THEME_QUERIES`. Only the `enchantment` theme is benchmark-validated (1 query,
  after 3 others were tested and cut for bringing zero benchmark hits — see §3).
  `artifact` and `graveyard` (2 queries each) are **unvalidated guesses** — no benchmark
  archetype exercises them yet.
- Query cap in `_retrieve()` raised from `queries[:8]` to `queries[:16]` to fit the
  worst case (base + tribal + theme + role queries).

## 2. Eval harness infrastructure

- `eval/archetypes/*.json`: `{commander, identity, query, max_card_price, good[], bad[]}`.
  Two scenarios exist: `aminatou_esper_enchantments.json` (Esper enchantments, themed)
  and `yargle_black_draw_engines.json` (mono-black, **textless** commander, generic
  "draw engine" ask — designed to isolate retrieval quality from theme detection).
- `scripts/eval_retrieval.py`: runs `DeckSolver._retrieve()` standalone (no LLM, no
  populated deck) against an archetype file, reports `candidate_pool` size, recall on
  `good`, hit rate on `bad`.
- `eval/scenarios/aminatou_esper_enchantments_full_deck.json`: fixture for a **selection**
  benchmark (given a contaminated pool + a real 99-card deck, does `fill()`/`cut()`
  still pick the bad cards?) — fixture exists, benchmark script does not. Not started.
- Import/export of `{query, deck}` state (`--import`/`--export`/`--dry-run` in
  `main_agent.py`, Import/Export buttons in the front-end Build tab) — built to make
  saving fixtures like the one above practical, not itself part of the eval harness.

### Measured baselines (`eval_retrieval.py`, Aminatou scenario)

| Stage | Pool size | Good recall | Bad hit rate |
|---|---|---|---|
| Original (generic `ROLE_QUERIES` only, no theme) | 270 | 1/10 (10%) | 9/10 (90%) |
| After theme detection + query pruning + 2 mislabeled benchmark entries fixed | 382 | 3/10 (30%) | 9/10 (90%) |

Yargle scenario (mono-black, textless, no theme signal): **0/10 good recall**, including
the user's own natural-language query — see §4.

## 3. What was tried and reverted (don't redo)

- **Type metadata as a Chroma `where`-filter channel** (`is_enchantment`/`is_artifact`
  bits, same pattern as the existing color bits). Added 31 candidates to the Aminatou
  pool, **zero** on either the good or bad list — pure noise, no recall gain. Fully
  reverted.
- **3 of 4 candidate `THEME_QUERIES["enchantment"]` phrases** (bare "enchantment",
  "constellation", "eerie"): brought zero good-list hits on the one archetype available
  to test them. Cut to the single query that did produce hits (`Mesa Enchantress`,
  `Monastery Siege`). Caveat: the benchmark only has 1-2 cards that could even validate
  these angles — this is "unproven on this archetype," not "proven useless in general."
- **`is_draw_engine` regex as a score bonus** (`ENGINE_BONUS` in `_score_parts`,
  gated on `role_bonuses["draw"] > 0`). Implemented, then reverted: a score bonus only
  reorders cards **already in** `candidate_pool` — it cannot fix a card never being
  retrieved in the first place, which is the actual failure mode (confirmed: reran the
  benchmark with the bonus in place, pool size and `good_missed` list were byte-identical
  to baseline). Sequencing lesson: validate "does it enter the pool" before "does it
  score well once in the pool" — the second question is moot without the first.
- **Deterministic draw-engine channel bypassing Chroma** (regex-scan the SQLite catalog
  directly for `is_draw_engine` + color/price filters, inject as extra candidates):
  proved it *can* find cards embedding search misses (confirmed for `Phyrexian Arena`,
  `Ashiok's Reaper`), but returns ~1244 candidates from one concept alone (vs. ~382 in
  today's whole pool) with no non-embedding way found to cut that down — tested ranking
  the 1244 by embedding distance (both full-doc and oracle-only), neither produced a
  usable top-40, and the two ranked inconsistently with each other. Not implemented; no
  cutoff strategy decided.
- **Generic/natural phrasing to replace per-card queries** ("at the beginning of your
  turn you draw cards", 5 variants tested): 0/5 phrasings found any of 5 known draw-engine
  targets. Confirms the mechanism is near-literal text overlap with a specific card's
  `oracle_text`, not concept understanding — a more "natural" query reliably does worse,
  not better.
- **`"draw a card"` vs `"draw cards"` (singular vs plural `ROLE_QUERY`)**: plural ranks
  marginally better on unrelated cards, but neither found any benchmark good-list card.
  No systematic quality difference demonstrated → kept `"draw a card"` as-is, no change.
- **Query decomposition, take 1** (breaking a long user query into short strategic
  phrases like "card advantage", "repeatable draw engine"): **0/10** targets found.
  Short alone isn't enough — the phrases still don't resemble how oracle text is written.
- **Query decomposition, take 2** (short phrases in oracle-text *register*, e.g. "at the
  beginning of your upkeep, draw a card"): **3/10** (`Phyrexian Arena` #30,
  `Morbid Opportunist` #10, `Necropotence` #36). Better than take 1, but at least one hit
  (`Necropotence`) turned out to be coincidental vocabulary overlap, not a real mechanical
  match — real signal is closer to 2/10.
- **Cross-encoder reranker**: not implemented (correctly, for now) — a reranker can only
  reorder candidates already retrieved; it cannot pull in a card that never made the
  candidate pool. Since 8 of the 10 Yargle targets don't appear even in the top 2000–6000
  of any embedding variant tested, a reranker has nothing to work with for most of them.
  Revisit only for the 1-2 targets that land "close" (rank ~30-170).

## 4. Root-cause findings (Yargle scenario: textless mono-black commander, natural-language
query, 10 known-good draw-engine targets, 0/10 baseline recall)

Four independent, additive causes of the embedding miss, each isolated with a dedicated
experiment (all scratch scripts, not committed):

| # | Cause | Evidence |
|---|---|---|
| 1 | **Document prefix** (`"{name} - {type_line}. Effect: "`) dilutes ~0.24 cosine on every indexed card, unconditionally | `oracle_text`-only cosine to `"draw a card"`: 0.70 → with prefix: 0.46 (same card, same query) |
| 2 | **Document length** dilutes proportionally, independent of relevance | Pearson r = **-0.323** between `oracle_text` word count and cosine-to-query, across 1096 cards that all literally contain "draw" |
| 3 | **Query length/structure** — long, compound natural sentences ("I want X or cards that Y...") degrade retrieval **regardless of topic** | Same degradation pattern reproduced in an unrelated domain (removal: `"destroy target creature"` → clean top-5 hits; `"I want repeatable removal effects or cards that let me destroy creatures"` → zero relevant top-5, same shape as the draw case) |
| 4 | NOT hubness — no "universal near-neighbor" cards found | 5 candidate noise cards tested against 5 unrelated queries; none appeared in any unrelated query's top-40 |

Removing the prefix (axis 1) helps but is not sufficient alone: only 1/10 targets reached
top-40 even in the best (`oracle_text`-only) reindex.

### Name vs. type_line ablation (does keeping type_line semantic help?)

Tested 3 document formats (`full` = production, `no_name` = type+effect only,
`oracle` = effect only) on the same 10 targets:

- Cosine score rises monotonically as text is stripped (full 0.330 → no_name 0.371 →
  oracle 0.414, mean over targets).
- **But rank does not track score**: removing only the name made rank *worse* for half
  the targets (`Phyrexian Arena` #1116→#2717, `Night's Whisper` #116→#2376) despite a
  higher absolute cosine. Cause: rank is competitive — shortening the document increases
  the *proportional share* that `type_line` (often a generic term like "Sorcery") takes
  up in what's left, which itself costs rank against the rest of the ~11,720-card corpus.
  **Conclusion: "keep type_line, drop only name" is not a safe, guaranteed improvement**
  — `oracle`-only (drop both) was the more consistently strong variant of the three.

### BM25 (pure lexical, zero embedding)

Hand-implemented (`rank_bm25` isn't installed in the venv), k1=1.5/b=0.75, run over the
same 11,720-card mono-black universe:

- Alone: **0/10 targets reach top-40** — same ceiling as embedding.
- But recovers half the targets (`Morbid Opportunist`, `Necropotence`, `Village Rites`,
  `Corrupted Conviction`) to rank ~200-600, vs. ~2000-4000 for any embedding variant on
  the same cards. The other half (`The One Ring`, `Liliana`, `Black Market Connections`)
  stays poor under both BM25 and embedding.
- **BM25 and embedding fail on different subsets of the same 10 cards** — the strongest
  evidence gathered for a hybrid (BM25 + embedding) approach over relying on either alone.

## 5. Bottom line / open decisions

- No fix that avoids touching the embedding model/index closes the Yargle case fully.
  The generic-query failure is structural (MiniLM mean-pooling + short-query/long-doc
  asymmetry), confirmed independently by Zuilho on an unrelated card/query pair
  (`notebooks/embedding_xploring.ipynb`, `Blasphemous Act` vs `"deal damage to all
  creatures"` — same shape of failure, same ad-hoc fix of using near-verbatim oracle
  text as the query).
- Per-card / per-trigger-phrase queries reliably work (parts 6-7 of the working notes)
  but don't scale — it's one query per phrasing, not a query per concept.
- Nothing here has been implemented in `solver.py`/`vectorize_cards.py` yet. Candidate
  next steps, none started: (a) prototype an actual BM25+cosine hybrid score, since it's
  the only lever shown to recover targets the embedding alone cannot reach without
  reindexing; (b) decide a non-embedding cutoff strategy for the deterministic
  draw-engine channel (§3) so it can be injected without inflating `candidate_pool` 3-4x;
  (c) any change to the embedded document format (dropping the name prefix, or moving to
  a retrieval-oriented model such as bge/e5) is a full reindex and belongs to Zuilho's
  side of the split — flag before touching.
