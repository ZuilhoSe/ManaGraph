# ONTOLOGY.md — Stage 3.6: functional ontology as the symbolic backbone

Companion to `RESEARCH.md`. Slots in **after Stage 3.5, before Stage 4 (TDA)**.

Sequence becomes: `1 DeckState → 2 fill/cut → 3 geometry → 3.5 mana/curve/roles → 3.6 ontology → 4 TDA → 5 epidemiology → 6 UI`.

---

## Why here and not later

Stage 3.5 made mana, curve and roles first-class. It left the synergy signal alone. That signal is still `cosine(commander_embedding, card_embedding)` over MiniLM oracle documents, and it is wrong in a specific, systematic way:

- Two board wipes are cosine-**near** and mutually **redundant**.
- A token producer and a token payoff are cosine-**far** and maximally **synergistic**.

MiniLM measures textual resemblance. Commander construction is driven by complementarity. The scorer is inverted exactly on the pairs that define a deck. `RESEARCH.md` already spotted one instance of this ("token producer vs token payoff are oracle classes, not a creature type") and hardcoded it. Stage 3.6 generalises that hardcode into a vocabulary.

`RESEARCH.md` also forbids running topology on a weak scorer. This is the largest available upgrade to the scorer, so it is the thing that unblocks Stage 4.

**Secondary effect on the NeSy claim.** Today the symbolic layer is *bookkeeping*: legality, singleton, identity, budget. Those are constraints, not reasoning. An ontology makes the objective symbolic too, which is what the architecture claim actually promises.

---

## Scope rules (freeze these before writing any predicate)

1. **A predicate exists only if a solver term, a hard constraint, or `diagnose_deck_json` consumes it.** Same rule as Stage 3.5: if it lives only in the prompt, this stage failed.
2. **Derive predicates from game mechanics, not from named archetypes.** Resources, zones, events, costs. Not "aristocrats", "spellslinger", "voltron". Naming the community's archetypes re-imports the popularity prior through the back door, in symbolic disguise, which would quietly destroy the discovery claim.
3. **Type, never value.** The ontology says a card *is* a sac outlet. It never says it is a *good* sac outlet. Quality is contextual and belongs to the solver's objective.
4. **No new representation without an ablation row.** Every predicate family added must be switchable off in the eval harness.
5. **Version and freeze** alongside the Scryfall bulk date and price snapshot: `schema_version`, `labels_version`, annotator model + prompt hash.

### Acceptance test for the whole stage

Two deliverables, both binary:

1. `diagnose_deck_json` reports at least one **typed deficit that the Stage 3.5 version cannot express**. Example: "9 treasure sources, 0 sacrifice outlets" or "4 reanimation effects, 2 legal reanimation targets".
2. The **cut picks different cards** because of that deficit, and the diff is explainable as a predicate mismatch rather than a cosine number.

If neither holds, the stage produced a taxonomy, not intelligence.

---

## The schema

Three layers. Only the first is annotated; the other two are computed.

### Layer 1 — Card predicates (annotated, deterministic once frozen)

Two currency families. This split is the whole design.

**Objects** — things that exist and can be counted.
`mana{W,U,B,R,G,C}`, `treasure`, `token(subtype, p/t)`, `+1/+1 counter`, `card_in_hand`, `life`, `creature_in_graveyard`, `card_in_graveyard`, `artifact_permanent`, `enchantment_permanent`, `land_in_play`, `card_in_exile`.

**Events** — things that happen and can be subscribed to.
`etb`, `death`, `sacrifice`, `cast_spell(type)`, `attack`, `deal_combat_damage`, `landfall`, `draw`, `discard`, `lifegain`, `untap`, `end_step`, `counter_placed`.

Predicate set over those:

| Predicate | Meaning | Example |
|---|---|---|
| `produces(Object, rate, cost)` | supplies an object | Sol Ring → mana, rate 2, cost 1 |
| `consumes(Object)` | requires an object as cost/fuel | Skullclamp → creature |
| `emits(Event)` | causes the event to occur | Krenko → etb (tokens) |
| `rewards(Event)` | triggers on the event | Impact Tremors → etb |
| `enables(Capability)` | removes a structural barrier | free sac outlet, haste grant, untapper, cost reduction |
| `answers(ThreatClass)` | interaction | creature, artifact, enchantment, board, stack, graveyard |
| `tutors(Selector)` | searches library for a class | creature, land, any |
| `recurs(zone_from, zone_to)` | moves cards between zones | graveyard → battlefield |
| `protects(TargetClass)` | hexproof, indestructible, counter-a-counter | commander, board |
| `requires(Precondition)` | a board/graveyard state to function | ≥3 creatures, non-empty graveyard |

Target: **60–120 predicate instances**, not more, in v1. Every one traceable to a consumer in `solver._score_parts`, `rules_validator`, or `diagnose_deck_json`.

### Layer 2 — Derived relations (computed, never annotated)

```
synergy(a, b)     ⟸  emits(a, E) ∧ rewards(b, E)
                  ∨   produces(a, R) ∧ consumes(b, R)
                  ∨   tutors(a, S) ∧ member(b, S)

redundant(a, b)   ⟸  same predicate signature
                  ∧   same cmc band
                  ∧   comparable rate

orphan(b)         ⟸  rewards(b, E) ∧ ¬∃a ∈ D : emits(a, E)
starved(b)        ⟸  consumes(b, R) ∧ supply(D, R) < threshold
dead(a)           ⟸  requires(a, P) ∧ ¬satisfied(D, P)
```

`redundant` replaces the current kNN-density cut heuristic, which conflates redundancy with thematic coherence.

### Layer 3 — Deck as a typed flow

A `DeckState` becomes a bipartite graph: emitters/producers on one side, subscribers/consumers on the other. Diagnosis is the **unmatched set**. The fill objective is a matching problem, which linearises cleanly into the ILP formulation Stage 2 already anticipated:

```
maximise   Σ w_R · min(supply_R, demand_R)      # matched pairs, saturating
         + Σ w_E · min(emitters_E, subscribers_E)
         - λ · redundancy(D)
subject to the existing hard constraints (99, identity, singleton, legality, P_max, B)
```

`min(...)` matters: it is what makes the objective refuse to stack twelve treasure producers with no outlet. A plain sum would not.

---

## Build plan

### Phase A — Contract and schema (≈1 week)

- [ ] Pin a Forge release and record its tag/commit in `catalog_meta`.
- [x] Write `data/ontology/schema_v1.yaml`: resources, events, predicates, capability enum, threat classes.
- [ ] For each predicate, name its **consumer** in code. Delete any predicate with no consumer.
- [ ] Define the gold-set sampling plan (below).
- [ ] Write the two acceptance tests as failing tests in `tests/test_ontology_acceptance.py`.

No annotation yet. Schema churn after annotation is the expensive failure mode.

### Phase B — Extraction pipeline (≈2–3 weeks)

Four tiers, cheapest first. Each card gets a `source` field recording which tier produced each label.

**Tier 0 — Forge bootstrap.** Run `scripts/mine_forge.py` against the pinned
`res/cardsfolder` release. Emit an intermediate artifact containing effects,
costs, triggers, `DeckHas` and `DeckNeeds`, plus a factual `card.facts` layer
for mana symbols, cost colours, types, subtypes, power/toughness, keywords and
DSL ability kinds. Candidate mappings to the mechanics-first schema remain a
separate layer. Forge seeds the event vocabulary and supplies a large
validation corpus; it does not define the final schema, does not contribute
runtime labels by itself, and its community archetype names are not imported.

The initial implementation accepts either a local directory or zip and records
`--release`/`--commit` (or `FORGE_RELEASE`/`FORGE_COMMIT`) in each JSONL row.
With neither supplied, provenance remains explicitly unset; the script never
downloads Forge. Faces, Oracle text, duplicate files, and resolvable
`SubAbility$` chains are retained for later cross-checking against Scryfall.
The cost colours in `card.facts` are evidence from Forge's `ManaCost`, not
Commander color identity; the latter remains a Scryfall fact.

**Tier 1 — structured Scryfall fields.** Free and exact. `keywords`, `produced_mana`, `type_line`, `power`/`toughness`, `mana_cost`. Covers most of `produces(mana)`, all keyword-derived `enables`, and type-based gates. You already persist `keywords` from Stage 3.5e.

**Tier 2 — template grammar over oracle text.** This is tractable for a reason worth stating in the paper: **oracle text is not free natural language.** WotC writes to a rigid templating manual. `Whenever ~ deals combat damage to a player`, `Sacrifice a creature:`, `Add {R}`, `When ~ enters, ...`, `Creatures you control get +1/+1`. A shallow pattern grammar covers a large fraction of the catalogue. `mana.py` already proves the approach.

Implement in `src/ontology/patterns.py`. Deterministic, unit-tested against pinned cards. Measure coverage per predicate as you go.

**Tier 3 — offline LLM batch annotator for the residual.** Run once over the ~30k unique oracle texts with structured output constrained to the schema. Freeze to `data/ontology/labels_v1.jsonl` with model name, prompt hash, and date. **Never at runtime.** This is the same split the project already committed to: model proposes, symbol decides. Cost is tens of dollars, not months.

- [x] `scripts/mine_forge.py` — parse a local Forge `cardsfolder` directory or zip
- [x] `data/ontology/forge_mapping.yaml` — versioned Forge-to-schema mapping
- [x] `src/ontology/schema.py` — dataclasses + enums
- [ ] `src/ontology/patterns.py` — tier 2
- [ ] `src/ontology/annotate.py` — orchestrates tiers, writes frozen artifact
- [ ] `python src/ontology/annotate.py --rebuild` wired into `build_dataset.py`

### Phase C — Validation (≈1 week, overlaps B)

You cannot claim a symbolic layer without measuring it.

**Gold set.** 300–500 cards, stratified by: set era (pre-Modern / Modern / recent), card type, rarity, oracle text length, and one deliberate over-sample of weird frames (MDFC, adventure, split, omen). Hand-label against the schema.

**Free ground truth: mine the Forge card scripts.** Forge's `res/cardsfolder` is over a decade of hand-written functional formalisation in a regular, flat DSL. Mapping its effect names onto your predicates gives tens of thousands of validation rows for the cost of a parser, and its `DeckHas` / `DeckNeeds` fields validate the *relational* layer too. **See Appendix A** for the engine comparison, the licence position, the mapping table, and the validation protocol. Run the protocol in the order given there; reversing steps 1 and 2 imports Forge's biases as ground truth without leaving a trace.

**Report per-predicate precision and recall.** Gate on it:

| Consumer | Minimum precision |
|---|---|
| Hard constraint / validator | 0.98 (or don't use it as a constraint) |
| Cut decision | 0.90 |
| Soft score term | 0.80 |
| Diagnosis text only | 0.70 |

A predicate below its gate stays in the schema but is switched off for that consumer. Publish the table; it is a paper asset, and it is the difference between an ontology and a pile of regex.

### Phase D — Make the solver consume it (≈2–3 weeks)

This is where the stage earns its keep. Nothing above matters without this.

- [ ] `src/ontology/graph.py` — derived relations, supply/demand tables
- [ ] `diagnose_deck_json` **v2**: event supply vs demand, orphan payoffs, starved consumers, dead cards, answer-class coverage, unmatched producers
- [ ] `solver._score_parts`: add the saturating matched-pair term
- [ ] Cut: swap kNN-density redundancy for predicate-signature redundancy
- [ ] `search_cards`: predicate filters (`emits=`, `rewards=`, `answers=`, `enables=`)
- [ ] Architect prompt consumes typed deficits; queries become `rewards=etb, cmc<=3` instead of `"good Krenko cards"`
- [ ] New view in `card_views.npz`: multi-hot predicate vector, built offline like the others

The last item is nearly free and buys a genuine ablation row.

### Phase E — Evaluation and ablation (≈1–2 weeks)

Extends the harness in `scripts/eval_selection.py`, on the same 3–5 commanders.

| Row | Retrieval | Score | Cut |
|---|---|---|---|
| A | MiniLM concat | 3.5 symbolic terms | kNN density |
| B | multi-view | 3.5 symbolic terms | kNN density |
| C | multi-view | + ontology matched-pairs | kNN density |
| D | multi-view | + ontology matched-pairs | predicate redundancy |
| E | **ontology filters only** | ontology only | predicate redundancy |

Row E is the interesting one. If a purely symbolic pipeline builds a coherent legal 99, that is a real and slightly uncomfortable finding, and it belongs in the paper either way.

New metrics on top of the `RESEARCH.md` set:
- matched-pair coverage (fraction of subscribers with a live emitter)
- orphan rate, starved rate, dead-card rate
- answer-class coverage (creature / artifact / enchantment / board / stack / graveyard)

**Sharper novelty definition.** The current one is "geometry-strong, observation-weak", measured in MiniLM space. Restate it as: *satisfies the same predicate signature as a known staple, at comparable cmc and rate, with near-zero inclusion rate.* That is a functional substitution claim rather than a textual-similarity claim, it is far more defensible, and it makes a much better figure.

---

## Timeline

| Phase | Weeks | Gate to proceed |
|---|---|---|
| A — schema | 1 | Every predicate has a named consumer |
| B — extraction | 2–3 | Tier 1+2 covers the top 40 predicates |
| C — validation | 1 (overlapping) | Per-predicate P/R table exists |
| D — consumption | 2–3 | Both acceptance tests pass |
| E — ablation | 1–2 | Rows A–E run end to end |

**≈7–10 weeks.** Then Stage 4 TDA, on a typed graph rather than a text point cloud.

**Kill criterion.** If tier 1+2 has not covered the top 40 predicates by the end of week 4, cut the schema to the 20 predicates the solver actually consumes and ship that. A small working ontology beats a large annotated one.

---

## Downstream effects

**Stage 4 (TDA).** Persistent homology over the typed synergy graph, or over the multi-hot predicate space, measures strategy structure. Over raw MiniLM it partly measures flavour-text vocabulary. H0 islands become "no card bridges these two event families", which is an actionable fill instruction rather than a diagram.

**Stage 5 (epidemiology).** This is the one that changes most. Contagion along `emits(a,E) → rewards(b,E)` edges is a transmission mechanism with a stated semantics. Contagion along cosine edges is arbitrary and a reviewer will say so. The ontology is what makes the second paper defensible.

**Publication.** The frozen, validated annotation is a citable artefact in its own right — a resource/dataset contribution, exportable as OWL/SKOS at the very end.

---

## Implementation notes

**Do not use OWL/RDF internally.** Description-logic reasoners are slow and you need no subsumption at runtime. Typed dataclasses plus SQLite tables plus a small predicate DSL is faster and easier to test. Export a standards-compliant OWL/SKOS artefact at publication time if you want the ontology-engineering credit. Fast inside, standard outside.

**Layout.**

```
src/ontology/
  schema.py      # enums + dataclasses, loaded from schema_v1.yaml
  patterns.py    # tier-2 template grammar
  annotate.py    # tier orchestration → frozen artifact
  graph.py       # derived relations, supply/demand
  diagnose.py    # typed deficits, feeds diagnose_deck_json
data/ontology/
  schema_v1.yaml
  labels_v1.jsonl     # frozen, not in git (size); rebuildable
  gold_v1.jsonl       # in git, small, hand-labelled
tests/
  test_ontology_patterns.py
  test_ontology_graph.py
  test_ontology_acceptance.py
```

`symbolic_cards.py` and `roles.py` become thin adapters over `src/ontology/` rather than parallel implementations. Migrate them; do not leave two vocabularies in the repo.

**Maintenance.** 4–6 sets a year, ~1500 new cards. `annotate.py --incremental` runs on the delta after each `scryfall_download`, and new-card labels go through the same tier ladder. Re-run the gold set annually to catch templating drift.

---

## What not to do

- Formalising the Comprehensive Rules. Zones, layers, priority, replacement effects. That is a card-text compiler; Forge and XMage each took over a decade. You do not need operational semantics to build a deck.
- Encoding card quality or power level as a predicate.
- Naming community archetypes as ontology classes.
- LLM annotation at runtime.
- Adding predicates before their consumer exists.
- Shipping a predicate into a hard constraint without a precision number behind it.
- Letting Phase A run past a week. Schema perfectionism is the black hole here.

---

# Appendix A — Mining Forge as the validation corpus

## Decision: Forge, not XMage

Both are full rules engines with near-complete card coverage. XMage has the friendlier licence (MIT) and implements every card as a Java class. Forge is GPL-3.0 and implements every card as a flat text script in `res/cardsfolder/`.

For this purpose the format decides it:

| | Forge | XMage |
|---|---|---|
| Card representation | flat `.txt` DSL, line-prefixed | Java class per card |
| Parser effort | ~40 lines of Python | Java AST + class-hierarchy resolution |
| Effect vocabulary | explicit API names (`Mana`, `Token`, `ChangeZone`) | constructor names across hundreds of effect classes |
| Costs | structural (`Cost$ Sac<1/Creature>`) | constructor arguments |
| Deckbuilding annotations | `DeckHas` / `DeckNeeds` / `DeckHints` | none equivalent |
| Licence | GPL-3.0 | MIT |

XMage is only worth touching if Forge's join coverage turns out to be worse than expected, which is unlikely.

## Licence posture

**Not legal advice.** The practical position:

- Forge is GPL-3.0. Copyleft attaches to derivative works of the *software*. Whether a label set derived from data files that the engine interprets is such a derivative is genuinely unsettled.
- The *fact* that Sol Ring produces two colourless is not protectable. The script's expression, and the selection and arrangement of the corpus, are.

**Rule for this project: Forge is an internal validation set. Nothing derived from it is redistributed.** Published labels come from the tier 1/2/3 pipeline in Phase B. The paper reports agreement against Forge as a quality metric.

This is also the better scientific position. "An annotation pipeline validated against an independent hand-built rules corpus" is a contribution. "We re-encoded Forge" is not, and it would couple the artefact's coverage to a third party's release cadence.

Do **not** embed the engine as a simulator to play out decks. Win rate is explicitly out of scope per `RESEARCH.md`.

## Three uses

**A1 — Seed the Event vocabulary from `T:Mode$`.**
Forge's trigger modes are an empirically derived enumeration of every event a Magic card can subscribe to, refined over fifteen years of scripting. Take that list as the seed for the `Event` enum in `schema_v1.yaml`, then prune to what deckbuilding consumes. This is a week saved and, more importantly, a vocabulary grounded in game mechanics rather than in community archetype names, which is what Scope Rule 2 demands.

**A2 — Bulk gold set for card predicates (replaces most of Phase C hand-labelling).**
Parse `cardsfolder` → apply the mapping table below → join on card name → compute per-predicate precision and recall for the tier 1/2/3 annotator against tens of thousands of rows instead of a few hundred.

**A3 — Validate the *relational* layer with `DeckHas` / `DeckNeeds`.**
Forge carries hand-curated deckbuilding annotations for its drafting AI. `DeckHas:Ability$Token` declares that a card supplies a capability; `DeckNeeds:Ability$Token|Counters` declares that a card demands one. That is the producer/consumer pairing of Layer 2, annotated by humans, and there is no equivalent in XMage.

This matters because hand-validating pairwise relations is combinatorially hopeless. Two constraints on its use:

- **Precision check only, never recall.** The tags are declaredly sparse; the wiki suggests roughly fifty cards as the minimum to bootstrap a new archetype. Absence of a tag means nothing.
- **Validate against it, never adopt its vocabulary.** The live values (`Counters`, `Graveyard`, `Token`) are community archetype names. Importing them into `schema_v1.yaml` is exactly the meta-leak Scope Rule 2 forbids.

## Parsing notes

- Scripts ship zipped at `res/cardsfolder/cardsfolder.zip`, one `.txt` per card.
- Filenames are lowercase, spaces become `_`, special characters dropped.
- Line prefixes: `A` ability effect, `T` triggered, `R` replacement, `S` static, `K` keyword, `SVar` variable, plus `Name` / `ManaCost` / `Types` / `PT` / `Oracle`.
- Ability prefixes inside `A:` — `SP$` spell, `AB$` activated, `DB$` drawback (chained sub-ability). Follow `SubAbility$` to walk the chain; a card's full effect set is the transitive closure, not the first line.
- Multi-face cards separate faces with a line containing `ALTERNATE`; the front face carries `AlternateMode:`.
- **Join key:** `Name:` against `cards.name`. Use the script's `Oracle:` field to cross-check the join and to detect drift between the Forge release and your Scryfall bulk snapshot. A mismatched Oracle means one of the two is stale; log it rather than silently accepting the labels.
- **Pin the Forge release tag** in `catalog_meta` alongside the Scryfall bulk date and the price snapshot. API names drift between releases.

## Mapping table (scaffold)

Verify each pattern against the `AbilityFactory`, `Triggers`, `Replacements`, `Statics` and `Costs` wiki pages before implementing. Treat this as the starting scaffold, not a finished spec; expect to iterate for several days, which is where the real work of A2 lives.

### Production

| Forge pattern | Predicate |
|---|---|
| `AB$ Mana \| Produced$ R` | `produces(mana_R)`, activated |
| `AB$ Mana \| Produced$ Any` | `produces(mana_*)` |
| `SP$/AB$ Token \| TokenScript$ c_a_treasure_sac` | `produces(treasure)` |
| `SP$/AB$ Token \| TokenScript$ …` | `produces(token(subtype,p/t))`, `emits(etb)` |
| `SP$/AB$ Draw` | `produces(card_in_hand)` |
| `SP$/AB$ Dig \| DestinationZone$ Hand` | `produces(card_in_hand)`, selective |
| `SP$/AB$ PutCounter \| CounterType$ P1P1` | `produces(p1p1_counter)` |
| `SP$/AB$ GainLife` | `produces(life)` |
| `SP$/AB$ Mill \| Defined$ You` | `produces(card_in_graveyard)` |
| `SP$/AB$ Play \| Valid$ Land` | `produces(land_in_play)`, extra land drop |
| `K:Cascade`, `K:Convoke`, `K:Affinity` | `enables(cost_reduction)` |

### Consumption (costs are where Forge beats regex)

| Forge pattern | Predicate |
|---|---|
| `Cost$ Sac<1/Creature>` | `consumes(creature)`, `enables(sac_outlet)`, `emits(sacrifice)` |
| `Cost$ Sac<1/CARDNAME>` | self-sacrifice; **not** a sac outlet — a common false positive for regex |
| `Cost$ Sac<1/Artifact>` | `consumes(artifact_permanent)`, `enables(sac_outlet)` |
| `Cost$ Discard<1/Card>` | `consumes(card_in_hand)`, `emits(discard)` |
| `Cost$ ExileFromGrave<…>` | `consumes(card_in_graveyard)` |
| `Cost$ PayLife<N>` | `consumes(life)` |
| `Cost$ tapXType<N/Creature>` | `consumes(creature)`, `enables(convoke_like)` |
| `Cost$ SubCounter<N/P1P1>` | `consumes(p1p1_counter)` |
| `Cost$ Return<1/Creature.YouCtrl>` | `consumes(creature)`, `emits(bounce)` |
| `Cost$ T` on a nonland permanent | tap-ability; feeds `enables(untap_payoff)` on the other side |

### Events emitted

| Forge pattern | Predicate |
|---|---|
| any `Token` effect | `emits(etb)`, `emits(token_created)` |
| `SP$/AB$ Sacrifice` | `emits(sacrifice)`, `emits(death)` |
| `SP$/AB$ ChangeZone \| Destination$ Battlefield` | `emits(etb)` |
| `SP$/AB$ Destroy \| Defined$ Self`, `DestroyAll` | `emits(death)` (mass) |
| `SP$/AB$ PutCounter` | `emits(counter_placed)` |
| `SP$/AB$ Untap` | `emits(untap)` |
| `SP$/AB$ Play \| Valid$ Land` | `emits(landfall)` |

### Events subscribed (the `rewards` side)

| Forge pattern | Predicate |
|---|---|
| `T:Mode$ ChangesZone \| Destination$ Battlefield` | `rewards(etb)` |
| `T:Mode$ ChangesZone \| Origin$ Battlefield \| Destination$ Graveyard` | `rewards(death)` |
| `T:Mode$ Sacrificed` | `rewards(sacrifice)` |
| `T:Mode$ Attacks` | `rewards(attack)` |
| `T:Mode$ DamageDone \| CombatDamage$ True` | `rewards(deal_combat_damage)` |
| `T:Mode$ SpellCast \| ValidCard$ Instant,Sorcery` | `rewards(cast_spell(instant\|sorcery))` |
| `T:Mode$ LandPlayed` | `rewards(landfall)` |
| `T:Mode$ Drawn` | `rewards(draw)` |
| `T:Mode$ Discarded` | `rewards(discard)` |
| `T:Mode$ CounterAdded \| CounterType$ P1P1` | `rewards(counter_placed)` |
| `T:Mode$ LifeGained` | `rewards(lifegain)` |
| `T:Mode$ TapsForMana` | `rewards(mana_produced)` |
| `T:Mode$ Phase \| Phase$ End of Turn` | `rewards(end_step)` |

The `emits`/`rewards` join over these two tables **is** the Layer 2 synergy relation. A1 through A3 exist to make it measurable.

### Interaction, tutors, recursion

| Forge pattern | Predicate |
|---|---|
| `SP$/AB$ Destroy \| ValidTgts$ Creature` | `answers(creature)` |
| `SP$/AB$ DestroyAll \| ValidCards$ Creature` | `answers(board)` |
| `SP$/AB$ Destroy \| ValidTgts$ Artifact,Enchantment` | `answers(artifact)`, `answers(enchantment)` |
| `SP$ Counter` | `answers(stack)` |
| `SP$/AB$ ChangeZone \| Origin$ Graveyard \| Destination$ Exile` | `answers(graveyard)` |
| `SP$/AB$ DealDamage \| ValidTgts$ Creature` | `answers(creature)`, damage-based |
| `SP$/AB$ ChangeZone \| Origin$ Library \| ChangeType$ Creature` | `tutors(creature)` |
| `SP$/AB$ ChangeZone \| Origin$ Library \| ChangeType$ Land` | `tutors(land)` |
| `SP$/AB$ ChangeZone \| Origin$ Graveyard \| Destination$ Battlefield` | `recurs(gy→bf)` |
| `SP$/AB$ ChangeZone \| Origin$ Graveyard \| Destination$ Hand` | `recurs(gy→hand)` |

`answers` breadth falls out of `Destroy` vs `DestroyAll` and of the `ValidTgts$` mask, which is far cleaner than inferring "board wipe" from oracle text.

### Enablers, statics, replacements

| Forge pattern | Predicate |
|---|---|
| `R:Event$ AddCounter` with a doubling effect | `amplifies(counter)` — Doubling Season class |
| `R:Event$ TokenCreated` doubling | `amplifies(token)` |
| `S:Mode$ Panharmonicon` | `amplifies(etb_trigger)` |
| `S:Mode$ ReduceCost` | `enables(cost_reduction)` |
| `S:Mode$ Continuous \| AddKeyword$ Haste` | `enables(haste_grant)` |
| `S:Mode$ Continuous \| AddKeyword$ Hexproof/Indestructible` | `protects(board)` |
| `K:Flash`, `K:Flying`, `K:Trample`, … | `enables(<keyword>)` direct from `K:` lines |
| `SP$/AB$ Untap \| Defined$ Self` on another permanent | `enables(untapper)` |

The replacement-effect doublers are the strongest argument for this whole appendix. Doubling Season, Parallel Lives and Panharmonicon are structurally identical in Forge and nearly impossible to catch reliably with oracle-text patterns, yet they are among the highest-leverage cards a Commander solver can identify.

### Deck-level (validation only, never imported into the schema)

| Forge field | Use |
|---|---|
| `DeckHas:Ability$X` | precision check on derived `emits`/`produces` |
| `DeckNeeds:Ability$X` | precision check on derived `rewards`/`consumes` |
| `DeckHints:Type$X` | weak signal; ignore unless A3 shows it is informative |
| `AI:RemoveDeck:All` | flag: card is unusable by an engine AI. Not a quality signal, do not treat it as one |

## Validation protocol

Order matters. Getting this backwards silently imports Forge's biases as ground truth.

1. **Hand-label ~50 cards**, stratified across the predicate families, blind to Forge.
2. **Validate the mapping table** against those 50. Measure how well Forge-derived labels match your hand labels. This is validating the *translation*, not the annotator.
3. Only once step 2 clears, **run the mapping over the full corpus** to produce `data/ontology/gold_forge.jsonl`.
4. **Evaluate the tier 1/2/3 annotator** against that gold set, per predicate, and fill in the precision table from Phase C.
5. Keep the 50 hand-labelled cards as a permanent held-out set. They are the only labels in the project that are independent of both Forge and the LLM annotator.

Report all three agreement numbers in the paper: hand vs Forge, hand vs pipeline, Forge vs pipeline. Disagreement between Forge and the pipeline is not automatically a pipeline error, and saying so honestly is worth more than a clean-looking single number.

## What Forge will not give you

Do not expect the mining step to close Phase B. It leaves:

- **Rates and magnitudes in comparable units.** `SVar:X:Count$…` makes many quantities dynamic. "Produces 2 mana" and "produces mana equal to the number of Goblins" need different handling and the solver needs both as numbers.
- **`requires(Precondition)`.** Board-state dependence is scattered across `NeedsToPlayVar`, `Condition*` params, and AI SVars. Partially recoverable, not cleanly.
- **Anything price-, plan-, or curve-related.** Not Forge's problem.
- **Redundancy.** Derived, Layer 2.
- **Cards Forge implements pragmatically** rather than literally, where the script is shaped by engine convenience. Rare, but it is a systematic source of disagreement worth logging rather than suppressing.
- **Un-cards and unimplementable cards.** Excluded upstream anyway.

## Deliverables

- [ ] `scripts/mine_forge.py` — parse `cardsfolder`, emit raw structured rows
- [ ] `data/ontology/forge_mapping.yaml` — the table above, versioned separately from the schema
- [ ] `data/ontology/gold_hand50.jsonl` — held out, in git, never regenerated
- [ ] `data/ontology/gold_forge.jsonl` — derived, **not** in git, not redistributed
- [ ] `scripts/eval_annotation.py` — three-way agreement report
- [ ] Forge release tag recorded in `catalog_meta`
