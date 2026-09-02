# Inventário: ontologia ManaGraph vs Forge

Snapshot do índice em `data/managraph.db` (consultado 2026-09-02 após `--reapply-mapping` 1.2.0). Mapping `1.2.0`, schema `1.0.0` (selectors extra: artifact/enchantment/instant/sorcery). Compiler NL (`compile_search_intent` / `requirement_families` / prompts do Architect) está fora do escopo deste arquivo.

Forge não é dependência de runtime. Autoridade factual: Scryfall/Oracle. Forge é corpus de bootstrap e validação.

Proveniência gravada em `catalog_meta`:

| chave | valor |
|---|---|
| `forge_path` | `C:\Users\segun\Documents\forge` |
| `forge_cardsfolder` | `C:\Users\segun\Documents\forge\forge-gui\res\cardsfolder` |
| `forge_release` | `daily-snapshots` |
| `forge_commit` | `2416d71ab4988b365b5bb0a006897c757a1d59d7` |
| `forge_mined_at` | `2026-08-29T03:52:35Z` |
| `forge_mined_records` | `33666` |
| `forge_mined_artifact` | `data/ontology/forge_raw_v1.jsonl` |
| `ontology_enriched_at` | `2026-08-29T11:14:32Z` |
| `ontology_mapping_reapplied_at` | `2026-09-02T04:57:58Z` |
| `ontology_model_rebuilt_at` | `2026-09-02T05:10:34Z` |
| `ontology_predicate_rows` | `83113` |
| `ontology_scryfall_cards` | `38651` |
| `ontology_forge_matches` | `33760` |

O cardsfolder local existe (33 666 `.txt`, igual a `forge_records`). Os nomes `AB$`/`SP$`/`DB$` abaixo vêm dos `record_json` já minerados (mesmo corpus). Não houve remine.

---

## 1. Arquivos da ontologia

| arquivo | papel |
|---|---|
| `data/ontology/schema_v1.yaml` | Contrato v1: objects, events, capabilities, threat_classes, selectors, target_classes, predicates + consumer declarado |
| `data/ontology/forge_mapping.yaml` | Tradução versionada `1.2.0`: regex Forge → `action` do miner |
| `data/ontology/model_config_v1.json` | Política Final (`scryfall-forge-final-v1`): de onde vem cada campo de `model_facts_json`. **Não cria nem filtra** `ontology_predicates` |
| `data/ontology/FORGE_COVERAGE.md` | Este inventário |
| `data/ontology/forge_raw_v1.jsonl` | Artefato minerado (um JSON por carta Forge) |
| `data/ontology/forge_scryfall_v1.jsonl` | Join Scryfall↔Forge emitido pelo enrich |
| `data/ontology/gold_set_v1.jsonl` | Export declarado do validador; **ainda não existe** (reviews = 0) |
| `src/ontology/schema.py` | Enums + loader YAML (`ObjectName`, `EventName`, `PredicateName`, `Capability`, `ThreatClass`, `Selector`, `TargetClass`, `Zone`) |
| `src/ontology/model_config.py` | Constrói Final: Scryfall card facts + Forge mechanics; **strip de SVar** em `mechanics.effects` |
| `src/ontology/search.py` | `flatten_candidates` → `ontology_predicates`; `rebuild_predicate_index`; busca por cláusula. Compiler NL neste módulo está fora de escopo |
| `src/ontology/__init__.py` | Reexporta schema (não exporta `Selector` / `TargetClass` / `Zone`) |
| `src/ontology/patterns.py` | **Não existe** (tier-2 Oracle grammar, planejado) |
| `src/ontology/annotate.py` | **Não existe** |
| `src/ontology/graph.py` | **Não existe** |
| `src/ontology/diagnose.py` | Déficits tipados; `diagnose_deck` e `rules_validator` leem o índice |
| `scripts/mine_forge.py` | Parser do cardsfolder + `apply_mapping` / `_action_candidates` |
| `scripts/enrich_ontology.py` | Join Scryfall↔Forge; `--reapply-mapping` reescreve candidates sem remine; chama `rebuild_predicate_index` |
| `scripts/_pred_counts.py` | Helper local de QA de counts (não é passo de pipeline) |
| `src/catalog.py` | DDL: `ontology_cards`, `forge_records`, `ontology_reviews`, `ontology_predicates` + índices |
| `src/service/handlers/ontology.py` | API do validador: stats, card, review, gold export, rebuild Final |
| `src/service/api.py` | Serve `/ontology-validator` e rotas de review |
| `data/ontology_validator.html` | UI de revisão humana (abas Scryfall / Forge / Final) |
| `src/hybrid_search.py` | **Lê** `ontology_predicates` via `search_ontology_clauses` |
| `src/solver.py` | Pool de fill chama `search_ontology_clauses`. `_score_parts` lê o índice (`ontology_pair`) |
| `ONTOLOGY.md` | Contrato de estágio + Appendix A (Forge). Declara consumers que ainda não leem o índice |
| `zuilho_plans/ontology-first-roadmap.md` | Roadmap (não é contrato executável) |
| `tests/test_ontology_schema.py` | Loader + enums + Forge não é runtime dep |
| `tests/test_mine_forge.py` | Parser + cada família de mapping (grant-only, cast_spell normalizado, bounce, extra combat, …) |
| `tests/test_ontology_review.py` | Join enrich / rematch / model rebuild |
| `tests/test_ontology_search.py` | Flatten + índice + busca (compiler coberto lá; fora daqui) |
| `tests/fixtures/forge/complex_card.txt` | Faces, SubAbility, Mana/Token/Draw/ChangeZone |
| `tests/fixtures/forge/sacrifice_outlet.txt` | `Cost$ Sac<1/Creature>` |
| `tests/fixtures/forge/duplicate_card.txt` | Dedup de filename |

### Tabelas SQLite (catálogo v5)

| tabela | papel |
|---|---|
| `cards` | Catálogo Scryfall canónico (38 651) |
| `ontology_cards` | Uma linha por carta Scryfall: join Forge + quatro camadas JSON (`canonical`, `resolved`, `model`, `forge`) + `forge_candidates_json` |
| `forge_records` | Uma linha por script Forge: `record_json`, `candidates_json`, match |
| `ontology_predicates` | Índice achatado (uma linha por `predicate`+`arg_key`+`arg_value`). **Pula** `validation_only` |
| `ontology_reviews` | Reviews humanas (0 linhas) |
| `catalog_meta` | Proveniência e contagens de enrich |

`model_facts_json` é a vista Final para o validador. O índice de predicados **não** lê Final: lê `forge_candidates_json` / `candidates_json`.

---

## 2. Vocabulário do schema v1

Fonte: `data/ontology/schema_v1.yaml` + enums em `src/ontology/schema.py`. `Zone` existe só no Python (argumentos de `recurs`). Preconditions **não** são enum no YAML; o miner congela quatro flags.

### objects

| object | parameters (schema) | quem escreve no índice |
|---|---|---|
| `mana` | `color` | `effect.mana` → `produces` (+ `color`/`rate`/`cost` extra) |
| `treasure` | — | `effect.token` se `TokenScript` contém `treasure` |
| `token` | `subtype`, `power`, `toughness` | `effect.token` (script vaza como `token_script`; `subtype` = 0 linhas) |
| `creature` | — | só `consumes` (`cost.sacrifice_creature`) |
| `p1p1_counter` | — | `effect.put_counter` se `CounterType` é `P1P1`/`11` |
| `card_in_hand` | — | `effect.draw` produz; `cost.discard_card` consome |
| `life` | — | `effect.gain_life` produz; `effect.lose_life` consome |
| `creature_in_graveyard` | — | `Mill`/`ChangeZone`/`Dig` só quando a máscara nomeia Creature (7 cards; mill genérico é `card_in_graveyard`) |
| `card_in_graveyard` | — | `Mill`, Surveil, library/hand→GY; `consumes` via ExileFromGrave |
| `artifact_permanent` | — | só `consumes` (`cost.sacrifice_artifact` / tapXType) |
| `enchantment_permanent` | — | Token `role_*` / Types Enchantment; ChangeZone dest BF + enchantment |
| `land_in_play` | — | `AdjustLandPlays$`, `Play` land, ChangeZone/Dig land→BF |
| `card_in_exile` | — | library/hand→exile (Impulse / Dig to exile) |

### events

| event | parameters | emits? | rewards? |
|---|---|---|---|
| `etb` | — | sim (Token, ChangeZone→BF) | sim (`ChangesZone` dest BF) |
| `death` | — | sim (Sacrifice, Destroy Self, DestroyAll) | sim (BF→GY) |
| `sacrifice` | — | sim (efeito + custo Sac) | sim (`Sacrificed`) |
| `cast_spell` | `type` | `Play` com ValidSA Spell/Instant/Sorcery/nonLand (não todo `SP$`) | sim (`SpellCast`; `type` ∈ {`any`,`creature`,`instant`,`sorcery`}) |
| `attack` | — | só via extra combat | sim (`Attacks`) |
| `deal_combat_damage` | — | extra combat + `DealDamage` com `CombatDamage$ True` | sim (`DamageDone` + `CombatDamage$ True`) |
| `landfall` | — | `AdjustLandPlays$` / Play land / land→BF | sim (`LandPlayed`) — 40 cartas |
| `draw` | — | `Draw` (além de `produces(card_in_hand)`) | sim (`Drawn`) |
| `discard` | — | só custo `Discard<1/Card>` | sim (`Discarded`) |
| `lifegain` | — | sim (`GainLife`) | sim (`LifeGained`) |
| `untap` | — | sim (`Untap`) | sim (`Untaps`) |
| `end_step` | — | **não** | sim (`Phase` + `End of Turn`) |
| `counter_placed` | — | sim (`PutCounter`) | sim (`CounterAdded`) |
| `mana_produced` | — | **não** | sim (`TapsForMana`) |
| `token_created` | — | sim (`Token` / Investigate / CopyPermanent) | sim (`T:Mode$ TokenCreated`) |
| `bounce` | — | sim (BF→Hand) | sim (`ChangesZone` BF→Hand) |

### capabilities

`sac_outlet` · `cost_reduction` · `haste_grant` · `untapper` · `convoke_like` · `keyword_grant` · `protection` · `extra_combat`

Todas as oito têm linhas no índice (ver §5). Nenhuma capability do schema está a 0.

### threat_classes

`creature` · `artifact` · `enchantment` · `board` · `stack` · `graveyard`

Todas as seis têm linhas. `_threat_classes` só lê as palavras `creature`/`artifact`/`enchantment` em `ValidTgts`/`ValidCards`/`Defined`. `board` vem de `DestroyAll`. `stack` vem de `Counter`. `graveyard` vem de ChangeZone GY→Exile.

### selectors

`creature` · `land` · `artifact` · `enchantment` · `instant` · `sorcery` · `any`

`_tutor_selector`: primeiro token de `ChangeType` após split `[.,+/]`. Tipos de schema passam; vazio/`card`/`any` → `any`; resto → **None**.

### target_classes

`commander` · `board`

`_protection_target_class`: markers voltron (`equippedcard`, `equippedby`, `enchantedcard`, `enchantedby`, `card.iscommander`) **ou** `ValidTgts` contém `creature` → `commander`; senão `board`.

### zones (enum Python, não lista YAML)

`library` · `hand` · `battlefield` · `graveyard` · `exile`

Só `graveyard→battlefield` e `graveyard→hand` entram em `recurs`. Exile aparece só como destino de `answers(graveyard)`.

### preconditions (não-enum)

`threshold` · `hellbent` · `metalcraft` · `delirium`

Só estas quatro. `DeckNeeds` não vira `requires`.

### predicates

| predicate | arguments (schema) | consumer declarado | o miner também escreve |
|---|---|---|---|
| `produces` | `object`, `rate`, `cost` | `diagnose_deck_json` | `color`, `token_script` (não no schema) |
| `consumes` | `object` | `diagnose_deck_json` | — |
| `emits` | `event` | `solver._score_parts` | — |
| `rewards` | `event` | `solver._score_parts` | `type` em `cast_spell` |
| `enables` | `capability` | `diagnose_deck_json` | — |
| `answers` | `threat_class` | `rules_validator` | — |
| `tutors` | `selector` | `diagnose_deck_json` | — |
| `recurs` | `zone_from`, `zone_to` | `solver._score_parts` | — |
| `protects` | `target_class` | `rules_validator` | — |
| `requires` | `precondition` | `diagnose_deck_json` | — |

`flatten_candidates` explode **cada** chave de `arguments` numa linha. Por isso `produces` tem 12 384 linhas mas só 4 352 `object=`.

Consumers declarados leem o índice: `diagnose_deck_json` (déficits), `solver._score_parts` (`ontology_pair`), `rules_validator` (`ontology_interaction` informativo). Ver §6.

---

## 3. O que o Forge entrega

O miner **não executa** Forge. Lê `.txt` (ou zip) e conserva DSL cru + facts estruturais.

### Shapes minerados (por face)

| campo | origem | entra no índice? |
|---|---|---|
| `effects` | linhas `A:` / `R:` / `S:` / `K:` / `SVar:` | só se um mapping `input: effects` casar |
| `costs` | `Cost$` extraído de cada effect | mappings `input: costs` |
| `triggers` | linhas `T:` | mappings `input: triggers` |
| `deck_has` / `deck_needs` / `deck_hints` | metadata | `validation_only` — **nunca** no índice |
| `subabilities` | fecho `SubAbility$` | evidência; mapping não percorre a cadeia |
| `card.facts` | ManaCost / Types / PT / K: | Final + validador; não vira predicado |
| `candidates` | `apply_mapping` | origem de `ontology_predicates` |

### Ability kinds parseados

Prefixo `_DSL_RE` = `A|T|R|S|K|SVar`. Contagens no corpus minerado (33 666 records):

| kind | prefix | records | mapping usa? |
|---|---|---|---|
| `ability` | `A` | 18 443 | sim (`AB$`/`SP$`/`DB$` no `value`) |
| `keyword` | `K` | 18 243 | só `keyword.convoke_like` (e grants se `K:` tivesse AddKeyword — não tem) |
| `trigger` | `T` | 16 962 | sim (`input: triggers`) |
| `static` | `S` | 7 088 | grants + `cost_reduction` (kind ∈ {static, ability}) |
| `replacement` | `R` | 1 693 | **parseado, nenhum mapping dedicado**. Vai para `effects`; regex `AB$`/`SP$`/`DB$` não casa. Doublers (`R:Event$ AddCounter` / `CreateToken`) ficam de fora |
| `svar` | `SVar` | 59 373 | `cost_reduction` e `requires_condition` **ignoram** kind=svar. Match de efeito usa o corpo após o primeiro `AB$`/`SP$`/`DB$` (`Nome:DB$ Token` casa). SubAbility resolve o nome do SVar |

Kinds que o parser **não** trata como DSL: qualquer linha que não case `_DSL_RE` nem `_METADATA_KEYS`. Campos novos de release vão para `metadata` cru.

### Efeitos AB$ / SP$ / DB$ (191 tipos únicos)

Mapped (nome do efeito, ocorrências no `record_json`, incluindo SVar):

| efeito | ocorrências | mapping |
|---|---|---|
| `ChangeZone` | 15 325 | `effect.change_zone` |
| `Draw` | 8 421 | `effect.draw` |
| `Token` | 7 961 | `effect.token` |
| `PutCounter` | 7 292 | `effect.put_counter` |
| `Mana` | 7 136 | `effect.mana` |
| `DealDamage` | 6 910 | `effect.deal_damage` |
| `Destroy` | 3 891 | `effect.destroy` |
| `GainLife` | 3 827 | `effect.gain_life` |
| `LoseLife` | 2 566 | `effect.lose_life` |
| `Sacrifice` | 1 913 | `effect.sacrifice` |
| `Counter` | 1 440 | `effect.counter` |
| `Untap` | 1 139 | `effect.untap` |
| `DestroyAll` | 890 | `effect.destroy_all` |
| `AddPhase` | 118 | `effect.add_combat` **só** se `ExtraPhase$ Combat` |

Candidatos `effect.token` (pré-1.2.0) = 2 556 vs 7 961 menções `Token`: o buraco era `SVar:…:DB$ Token`. Mapping 1.2.0 faz match no corpo `DB$` do SVar; `produces(token)` = 3 435 cards, `emits(token_created)` = 3 758.

### Triggers `T:Mode$` — parseamos todos; mapeamos 13 modos

Mapeados: `ChangesZone` (dois recortes), `Sacrificed`, `Attacks`, `DamageDone`+`CombatDamage$ True`, `SpellCast`, `LandPlayed`, `Drawn`, `Discarded`, `CounterAdded`, `LifeGained`, `TapsForMana`, `Phase`+`End of Turn`, `Untaps`.

`Phase` no cardsfolder ≈ 2 377; só 885 viram `rewards(end_step)`. O resto (upkeep, begin combat, …) é parseado e dropado.

Não mapeados com ≥20 ocorrências no cardsfolder: ver §6.

### Statics `S:Mode$` (top)

`Continuous` 4810 · `ReduceCost` 527 · `CantBlockBy` 364 · `AlternativeCost` 148 · `CantBlock` 139 · `CantAttack` 120 · `MustAttack` 101 · `CantBeCast` 101 · `RaiseCost` 95 · `Panharmonicon` 38 · …

Só `ReduceCost` / `SetColorlessCost` e grants `AddKeyword`/`KW$` dentro de Continuous entram no índice. `Panharmonicon` (38) não tem predicado (`amplifies` saiu do schema).

### Replacements `R:Event$` (top)

`Moved` 957 · `DamageDone` 218 · `Untap` 156 · `Counter` 117 · `Draw` 37 · `CreateToken` 32 · `AddCounter` 32 · …

Nenhum vira predicado. Appendix A de `ONTOLOGY.md` queria `amplifies(counter|token|etb_trigger)` — **não implementado**.

### Keywords `K:`

Parseadas para `card.facts.keywords`. **Self** `K:Hexproof` / `K:Haste` / `K:Flying` **não** indexam `protects` / `haste_grant` / `keyword_grant`. Self `K:Convoke|Delve|Improvise` **sim** (`keyword.convoke_like`).

### DeckHas / DeckNeeds / DeckHints

Conservados em Final (`field_sources: forge`). Mapping `validation.*` gera candidates `validation_only: true`. `flatten_candidates` descarta. 12 779 candidates de validação no join atual.

### Custos parseados vs mapeados

Qualquer `Cost$` vira linha em `costs`. Só estes regexes geram predicados:

- `Sac<1/Creature>` → sac outlet
- `Sac<1/Artifact>` → sac outlet de artefato
- `Sac<1/CARDNAME>` → **ignore** (não é outlet)
- `Discard<1/Card>` → consume hand + emit discard
- `ExileFromGrave<` → consume graveyard

Não mapeados (Appendix A): `PayLife`, `tapXType`, `SubCounter<N/P1P1>`, `Return<1/Creature…>`, tap-only em não-terreno.

---

## 4. Mapping 1.2.0 (o que traduzimos)

`data/ontology/forge_mapping.yaml`. `action` implementada em `_action_candidates`. Contagens = candidates **antes** do flatten (inclui `validation_only`). `cost.sacrifice_self` produz 0 candidates (ação `ignore_self_sacrifice`).

| id | input | action | predicado(s) | candidates |
|---|---|---|---|---|
| `effect.mana` | effects | `mana` | `produces(mana, color, rate, cost)` | 2144 |
| `effect.token` | effects | `token` | `produces(treasure\|token)` + `emits(etb)` + `emits(token_created)` | 2556 |
| `effect.draw` | effects | `draw` | `produces(card_in_hand, rate)` | 969 |
| `effect.change_zone` | effects | `change_zone` | ver ramos abaixo | 2261 |
| `effect.sacrifice` | effects | `sacrifice` | `emits(sacrifice)`, `emits(death)` | 258 |
| `effect.destroy` | effects | `destroy` | `answers(threat)` **ou** `emits(death)` se `Defined$ Self` | 806 |
| `effect.destroy_all` | effects | `destroy_all` | `answers(board)`, `emits(death)` | 324 |
| `effect.counter` | effects | `counter_spell` | `answers(stack)` | 372 |
| `effect.gain_life` | effects | `gain_life` | `produces(life)`, `emits(lifegain)` | 554 |
| `effect.lose_life` | effects | `lose_life` | `consumes(life)` | 141 |
| `effect.untap` | effects | `untap` | `emits(untap)`; `enables(untapper)` se **não** self | 345 |
| `effect.put_counter` | effects | `put_counter` | `emits(counter_placed)`; `produces(p1p1_counter)` se P1P1 | 1126 |
| `effect.add_combat` | effects | `extra_combat` | `enables(extra_combat)`, `emits(attack)` | 100 |
| `effect.deal_damage` | effects | `deal_damage` | `answers` só se ValidTgts/Cards nomeia creature/artifact/enchantment | 915 |
| `effect.grant_protection` | effects | `grant_protection` | `protects(target_class)`, `enables(protection)` | 1458 |
| `effect.grant_haste` | effects | `grant_haste` | `enables(haste_grant)` | 448 |
| `effect.grant_keyword` | effects | `grant_keyword` | `enables(keyword_grant)` | 1570 |
| `keyword.convoke_like` | effects | `convoke_like` | `enables(convoke_like)` se kind=`keyword` | 158 |
| `effect.cost_reduction` | effects | `cost_reduction` | `enables(cost_reduction)` se kind ∈ {static, ability} | 580 |
| `effect.requires` | effects | `requires_condition` | `requires(precondition)` | 138 |
| `cost.sacrifice_creature` | costs | `sacrifice_cost` | `consumes(creature)`, `enables(sac_outlet)`, `emits(sacrifice)` | 144 |
| `cost.sacrifice_artifact` | costs | `sacrifice_cost_artifact` | `consumes(artifact_permanent)`, `enables(sac_outlet)`, `emits(sacrifice)` | 75 |
| `cost.sacrifice_self` | costs | `ignore_self_sacrifice` | **nada** | 0 |
| `cost.discard_card` | costs | `discard_cost` | `consumes(card_in_hand)`, `emits(discard)` | 258 |
| `cost.exile_graveyard` | costs | `exile_graveyard_cost` | `consumes(card_in_graveyard)` | 41 |
| `trigger.changes_zone_battlefield` | triggers | `reward_etb` | `rewards(etb)` | 5853 |
| `trigger.changes_zone_death` | triggers | `reward_death` | `rewards(death)` | 1374 |
| `trigger.sacrificed` | triggers | `reward_sacrifice` | `rewards(sacrifice)` | 114 |
| `trigger.attacks` | triggers | `reward_attack` | `rewards(attack)` | 1596 |
| `trigger.combat_damage` | triggers | `reward_combat_damage` | `rewards(deal_combat_damage)` | 835 |
| `trigger.spell_cast` | triggers | `reward_cast_spell` | `rewards(cast_spell[, type])` | 1428 |
| `trigger.landfall` | triggers | `reward_landfall` | `rewards(landfall)` | 42 |
| `trigger.drawn` | triggers | `reward_draw` | `rewards(draw)` | 149 |
| `trigger.discarded` | triggers | `reward_discard` | `rewards(discard)` | 104 |
| `trigger.counter_added` | triggers | `reward_counter_placed` | `rewards(counter_placed)` | 68 |
| `trigger.life_gained` | triggers | `reward_lifegain` | `rewards(lifegain)` | 93 |
| `trigger.taps_for_mana` | triggers | `reward_mana_produced` | `rewards(mana_produced)` | 65 |
| `trigger.end_step` | triggers | `reward_end_step` | `rewards(end_step)` | 885 |
| `trigger.untaps` | triggers | `reward_untap` | `rewards(untap)` | 30 |
| `trigger.requires` | triggers | `requires_condition` | `requires(precondition)` | 38 |
| `validation.deck_has` | deck_has | `validate_deck_has` | validation_only | 7512 |
| `validation.deck_needs` | deck_needs | `validate_deck_needs` | validation_only | 1206 |
| `validation.deck_hints` | deck_hints | `validate_deck_hints` | validation_only | 4061 |

Ação órfã no miner (não referenciada no YAML): `reward_spell_cast` (alias de `reward_cast_spell`). Ações sem `if` devolvem `[]`.

### Ramos de `change_zone`

| Origin → Destination | predicados |
|---|---|
| `*` → `battlefield` | `emits(etb)` |
| `graveyard` → `battlefield` | + `recurs(graveyard, battlefield)` |
| `graveyard` → `hand` | + `recurs(graveyard, hand)` |
| `library` → `hand` ou `battlefield` | + `tutors(selector)` se `_tutor_selector` não for None |
| `graveyard` → `exile` | + `answers(graveyard)` |
| `battlefield` → `hand` | + `emits(bounce)` + `answers` por ValidTgts |
| `battlefield` → `exile` | + `answers` por ValidTgts |
| outros (library→library, hand→GY, …) | nada além de `etb` se dest=BF |

### Grant-only vs self-keyword

| regra | casa | **não** casa |
|---|---|---|
| `effect.grant_protection` | `AddKeyword`/`AddHiddenKeyword`/`KW$` com Hexproof, Shroud, Indestructible, Protection | `K:Hexproof` (self) |
| `effect.grant_haste` | mesmo, com `\bHaste\b` | `K:Haste` |
| `effect.grant_keyword` | Flying, Menace, Trample, Double Strike, Unblockable, Shadow, Horsemanship, Skulk | self `K:Flying` etc.; Hexproof fica no grant_protection |
| `keyword.convoke_like` | `K:Convoke` / `Delve` / `Improvise` | SVar cujo nome começa com Convoke |

Pump no corpus: 6 036 linhas `Pump`/`PumpAll`; 1 499 também têm KW$ de grant e **são** indexadas via as três regras de grant, não via ação `pump` (não existe).

Exemplos: Lightning Greaves (`K:Hexproof` no equipped via static) → `protects(commander)` + `enables(protection)`. Uma criatura com só `K:Hexproof` → zero `protects`.

---

## 5. O que está no índice agora

`ontology_predicates`: **83 113** linhas · **25 205** `card_id` distintos (mapping 1.2.0). As tabelas detalhadas abaixo misturam o snapshot 1.1.0 com notas; counts atuais dos objects/events que estavam a 0 estão no §6.

| universo | n |
|---|---|
| `cards` | 38 651 |
| `ontology_cards` | 38 651 |
| ontology matched / unmatched | 33 760 / 4 891 |
| `forge_records` | 33 666 |
| forge matched / unmatched | 33 609 / 57 |
| ontology_cards com candidates | 22 977 |
| candidates predicado / validation_only | 30 415 / 12 779 |
| `ontology_reviews` | 0 |

Cartas matched sem predicado indexado ≈ 33 760 − 20 218 ≈ **13 5xx** (Forge casou mas mapping não emitiu predicado, ou só validation_only).

Unmatched Scryfall por `layout` (intencional vs falha de join):

| layout | n | nota |
|---|---|---|
| `art_series` | 2243 | skip proposital |
| `normal` | 1275 | falha de join / Forge ausente |
| `token` | 817 | skip |
| `front_card` | 275 | |
| `emblem` | 87 | skip |
| `double_faced_token` | 80 | skip |
| `planar` | 56 | skip |
| `split` / `augment` / `meld` / `flip` / `transform` / `adventure` / `saga` / `host` / `vanguard` / `prepare` | 14+14+7+6+5+4+3+3+1+1 | residual |

### Contagens por predicado

| predicate | linhas | cards distintos |
|---|---|---|
| `rewards` | 13 536 | 11 201 |
| `produces` | 12 384 | 4 051 |
| `emits` | 4 385 | 3 312 |
| `enables` | 3 685 | 3 432 |
| `answers` | 2 780 | 2 542 |
| `recurs` | 1 078 | 538 |
| `protects` | 700 | 692 |
| `consumes` | 381 | 376 |
| `tutors` | 232 | 231 |
| `requires` | 165 | 165 |

### `enables` / capability

| capability | linhas | cards | amostra |
|---|---|---|---|
| `keyword_grant` | 1535 | 1532 | A-Armory Veteran, Accelerate (grant, não self flying) |
| `protection` | 697 | 692 | A-The One Ring, Absolute Grace |
| `cost_reduction` | 563 | 563 | A-Cosmos Charger |
| `haste_grant` | 443 | 443 | Agatha of the Vile Cauldron |
| `untapper` | 167 | 167 | Aphetto Alchemist |
| `convoke_like` | 157 | 157 | Ancient Imperiosaur, Afterlife from the Loam |
| `sac_outlet` | 73 | 73 | Ashnod's Altar, Altar of Dementia, Arcbound Ravager |
| `extra_combat` | 50 | 50 | Aggravated Assault, Anzrag, the Quake-Mole, Akki Battle Squad |

### `produces` / object

| object | cards | nota |
|---|---|---|
| `mana` | 1875 | + 2108 linhas `color` (77 valores distintos) |
| `card_in_hand` | 945 | Draw; **não** emite `draw` |
| `token` | 803 | + 850 `token_script` (388 scripts) |
| `p1p1_counter` | 413 | |
| `life` | 273 | |
| `treasure` | 39 | só TokenScript com `treasure` |

`produces` extra (não-schema): `rate` 3081 (27 valores; lixo `Count$Valid…`, `SVar$X/Times.3`) · `color` 2108 · `cost` 1993 (134 valores Forge: `T`, `T Sac<1/CARDNAME>`, …) · `token_script` 850 · `subtype` **0**.

### `consumes` / object

| object | cards |
|---|---|
| `life` | 141 |
| `card_in_hand` | 129 |
| `creature` | 48 |
| `card_in_graveyard` | 37 |
| `artifact_permanent` | 25 |

### `emits` / event

| event | cards |
|---|---|
| `etb` | 1427 |
| `token_created` | 838 |
| `counter_placed` | 698 |
| `bounce` | 306 |
| `death` | 293 |
| `lifegain` | 273 |
| `sacrifice` | 200 |
| `untap` | 168 |
| `discard` | 129 |
| `attack` | 50 |

### `rewards` / event

| event | cards |
|---|---|
| `etb` | 5702 |
| `attack` | 1570 |
| `cast_spell` | 1375 |
| `death` | 1345 |
| `end_step` | 865 |
| `deal_combat_damage` | 821 |
| `draw` | 145 |
| `sacrifice` | 113 |
| `discard` | 97 |
| `lifegain` | 93 |
| `counter_placed` | 68 |
| `mana_produced` | 64 |
| `landfall` | 40 |
| `untap` | 30 |

`rewards` / `type` (cast_spell): `any` 1047 · `creature` 114 · `instant` 8 · `sorcery` 2. **Zero** `Card.Blue` / vírgulas / `Instant.Blue` (normalização `_cast_spell_arguments`).

### `answers` / threat_class

creature 1666 · stack 369 · artifact 260 · enchantment 197 · board 161 · graveyard 124

### `tutors` / selector

land 136 · any 52 · creature 44

### `recurs`

graveyard→battlefield 279 cards · graveyard→hand 259 cards

### `protects` / target_class

board 492 · commander 203

### `requires` / precondition

threshold 72 · delirium 50 · metalcraft 25 · hellbent 18

### Lixo / DSL vazado em `arg_value`

`cast_spell.type` está limpo. O vazamento está em `produces`:

| arg_key | exemplo | n |
|---|---|---|
| `color` | `Combo R G`, `Combo Any`, `Chosen`, `Combo ColorIdentity`, `Special EachColorAmong_Valid Permanent.YouCtrl` | dezenas cada Combo |
| `rate` | `Count$Valid Artifact.YouCtrl`, `SVar$X/Times.3`, `Sacrificed$CardPower`, `UrzaAmount` | 1–3 |
| `cost` | `T Sac<1/CARDNAME>`, `T PayLife<1>`, `tapXType<1/Creature.Blue>` | costs Forge crus (schema pede `cost`, então é evidência retida, não selector) |
| `token_script` | `c_a_treasure_sac`, listas com vírgula `g_1_1_snake,g_2_2_wolf,…` | 850 linhas / 388 distinct |

`Any` em Produced$ é `color=any`. `Combo R G` / `Chosen` / ColorIdentity **não** entram em `arg_value` (ficam em evidence). `rate` só inteiro. `token_script` fica em evidence, não no índice.

---

## 6. O que falta (pós mapping 1.2.0)

Passagem de 2026-09-02: SVar `DB$`, objects/events a 0, DSL de alto volume mapeável, leaks, e consumers declarados. `--reapply-mapping` sem remine. Índice: **83 113** linhas · **25 205** cards.

### Fechado nesta passagem

| buraco | o que foi feito | count agora |
|---|---|---|
| SVar `Nome:DB$ Token/Draw/ChangeZone` | `apply_mapping` faz match no corpo `AB$`/`SP$`/`DB$`; SubAbility resolve SVar por nome | `produces(token)` 3 435 cards (era 803); `card_in_hand` 4 049 (era 945); `emits(draw)` 3 566; `emits(token_created)` 3 758 |
| `land_in_play` = 0 | `AdjustLandPlays$` (Azusa/Exploration), `Play` land, ChangeZone/Dig land→BF | 462 cards |
| `card_in_exile` = 0 | library/hand → exile | 681 cards |
| `enchantment_permanent` = 0 | Token `role_*` / Types Enchantment; ChangeZone dest BF | 80 cards |
| `creature_in_graveyard` = 0 | Mill/ChangeZone/Dig **só** se a máscara nomeia Creature | 7 cards (estreito; mill genérico é `card_in_graveyard` = 896) |
| `card_in_graveyard` só consume | Mill, Surveil, library/hand→GY | 896 produce |
| emits `draw` / `landfall` / `cast_spell` / `deal_combat_damage` / `mana_produced` | Draw; land ETB / extra land; `Play` spell (não todo `SP$`); extra combat + DealDamage combat; Mana | 3 566 / 462 / 267 / 51 / 2 178 cards |
| rewards `token_created` / `bounce` | `T:Mode$ TokenCreated`; `ChangesZone` BF→Hand | 5 / 7 cards (Forge tem poucos modos) |
| Mill / ChangeZoneAll / DamageAll / Fight / Play / Dig / SetLife / CopyPermanent | ações no YAML 1.2.0 | ver §4 + counts acima |
| Discard efeito (Defined You), Surveil, Investigate, Proliferate, PutCounterAll, SacrificeAll, UntapAll | mapeados para predicados existentes | — |
| PayLife / tapXType / SubCounter P1P1 / Return creature / Sac Permanent+subtype+X | consumes + outlets | `consumes(life)` 1 237; `sac_outlet` 313 (era 73) |
| `K:Cascade` / `K:Affinity` | `enables(cost_reduction)` | cost_reduction 688 (era 563) |
| tutors Artifact/Instant/Enchantment/Sorcery | selectors novos no schema | artifact 69 · instant 21 · enchantment 17 · sorcery 1 |
| `Combo R G` / `Chosen` / `token_script` / rate SVar | color só WUBRGC/any; rate só int; token_script só evidence | 0 leaks |
| `diagnose_deck_json` | `src/ontology/diagnose.py` — tesouros vs outlet, tokens vs payoff, extra combat, reanimação vs mill | lê o índice |
| `solver._score_parts` | termo `ontology_pair` (emits↔rewards, produces↔consumes); `MANAGRAPH_ONTOLOGY_SCORE=0` desliga | lê o índice |
| Extra land real no Forge | **não** é `Play Valid$ Land`; é `S:Mode$ Continuous \| AdjustLandPlays$ N` | 462 |

### Consumers

| consumer | agora |
|---|---|
| `diagnose_deck_json` | curva/pips/roles **e** déficits tipados do índice (`ontology_deficits`) |
| `solver._score_parts` | Jaccard/geometry/roles **e** complementaridade saturante (`ontology_pair`) |
| `rules_validator` | **não** inventa regra. `answers`/`protects` não são legalidade Commander. O validador **lê** o índice e anexa `ontology_interaction` (contagens); `valid` não muda |

Quem mais lê o índice: `search_ontology_clauses`, `hybrid_search`, pool de fill do solver.

### Ainda impossível (sem mentir / sem predicado novo)

- **`AddTurn` (166) ≠ extra combat.** Mapping `ignore_add_turn` devolve `[]`. Extra turn fica de fora de propósito.
- **`end_step` emit** — o passo do turno não é causado por uma carta. Só `rewards(end_step)`.
- **`cast_spell` emit em todo instant/sorcery** — recusado (flood). Só `Play` com ValidSA Spell/Instant/Sorcery/nonLand.
- **`deal_combat_damage` emit em toda criatura** — recusado. Só extra combat e DealDamage com `CombatDamage$ True` (51 cards).
- **`creature_in_graveyard` em mill genérico** — mill de cards não afirma criaturas. 7 cards com máscara Creature; o resto é `card_in_graveyard`.
- **Pump / PumpAll +N/+N sem KW$** — não há predicado de pump; grant-only hexproof/haste/evasion mantido.
- **Cleanup, Effect, Charm** — wrappers. Charm é `ignore_charm`; ramos `DB$`/SVar é que mapeiam.
- **Tap / Animate / Clone-as-self / Scry / Fog / GainControl / Reveal** — sem capability/object honesto.
- **CopySpellAbility** — cópia no stack ≠ `cast_spell` nas regras.
- **SetLife em oponente** — não é `consumes(life)` do dono da carta. Só `Defined$ You` → `produces(life)`.
- **`S:Mode$ Panharmonicon` / `R:Event$ AddCounter|CreateToken`** — Appendix A pedia `amplifies`, **removido do schema**. Sem predicado.
- **`requires` ≥3 criaturas / GY não vazia** — Forge não tem flag limpa; `NeedsToPlayVar` / `Condition*` além das 4 flags ficam de fora. DeckNeeds continua `validation_only`.
- **Food / Clue / Powerstone como `treasure`** — decisão 9: Treasure só por TokenScript explícito.
- **AddPhase não-Combat** — não é `extra_combat`.
- **ETB de criatura “normal”** — ainda não `emits(etb)` (só Token / ChangeZone→BF / CopyPermanent). `rewards(etb)` >> `emits(etb)` no sentido inverso agora (emits etb 5 717 ≈ rewards 5 702).
- **`src/ontology/graph.py` / `patterns.py` / `annotate.py`** — ainda não existem. `diagnose.py` passou a existir.
- **Gold set** — reviews = 0.

### DSL ainda sem ação própria (e sem predicado honesto)

Pump, Cleanup, Effect, Tap, PumpAll, Animate, Scry, DelayedTrigger, ChooseCard, RepeatEach, GainControl, Regenerate, SetState, CopySpellAbility, Attach, RemoveCounter, ChooseType, MakeCard, PeekAndReveal, Clone, GenericChoice, PreventDamage, AnimateAll, ReplaceEffect, RollDice, ChooseColor, StoreSVar, ChoosePlayer, Branch, AlterAttribute, NameCard, Reveal, FlipCoin, TapAll, **AddTurn**, Phases, Amass, Goad, Debuff, Fog, Discover, Manifest, Replace*, Vote, … (lista longa inalterada para o que não tem object/event/capability).

Triggers ainda sem evento no schema: AttackersDeclared, ChaosEnsues, Blocks, TurnFaceUp, BecomesTarget, Cycled, Always, Contraption, Transformed, Mutates, … `DamageDone` sem `CombatDamage$ True` continua sem `rewards`. `CounterAddedOnce` já casa o regex de `CounterAdded`.

### Predicados ainda estreitos (não zero)

- `requires` 165 — só 4 flags
- `creature_in_graveyard` produce 7
- `rewards(token_created)` 5 / `rewards(bounce)` 7 — Forge quase não tem esses modos
- `extra_combat` 50 vs AddPhase 118 (não-Combat de fora)
- `treasure` 369 (subiu com SVar; Food/Clue continuam `token`)
- `tutors(sorcery)` 1 — raro no ChangeType isolado

---

## 7. Decisões de desenho já tomadas

1. **Grant-only hexproof / shroud / indestructible / protection.** Self `K:Hexproof` não é `protects`. Equipment/aura/pump que **concedem** a keyword sim. Voltron (`EquippedBy`, `Card.IsCommander`, ValidTgts creature) → `target_class=commander`; senão `board`.
2. **Grant-only haste.** Self `K:Haste` ≠ `haste_grant`. Só `AddKeyword`/`KW$ Haste`.
3. **Grant-only evasion keywords.** Flying/Menace/Trample/Double Strike/Unblockable/Shadow/Horsemanship/Skulk só como grant → `keyword_grant`. Self keywords ficam em Scryfall `keywords` / Forge facts, não no índice de predicados.
4. **Convoke-like é self-keyword.** `K:Convoke|Delve|Improvise`. SVar com esse nome é ignorado (`kind != keyword`).
5. **DeckNeeds / DeckHas / DeckHints ficam `validation_only`.** Não entram em `schema_v1.yaml` nem em `ontology_predicates`. Vocabulário de arquétipo do Forge (`Ability$Token`, `Counters`) não vaza para predicates.
6. **Extra turn ≠ extra combat.** `AddPhase ExtraPhase$ Combat` → `extra_combat`. `AddTurn` (166) não mapeia. Queries “extra turn” sem “combat” não devem colidir (compiler fora de escopo; a decisão está no miner + mapping).
7. **`cast_spell` type normalizado.** `_CAST_SPELL_TYPES` = {creature, instant, sorcery}. Um tipo → `type=that`. Zero tipos → `type=any`. Dois+ (`Instant,Sorcery`) → **omite** `type` (não explode, não vaza `Card.Blue`).
8. **`Produced$ Any` não expande cores.** Fica `color=any`.
9. **Treasure só por TokenScript explícito.** Não inferir Treasure de Oracle.
10. **Self-sacrifice (`Sac<1/CARDNAME>`) não é sac outlet.**
11. **Untap self só emite; untap de outro permanente habilita `untapper`.**
12. **DealDamage em player/qualquer sem creature/artifact/enchantment no mask → zero `answers`.**
13. **Tutor só library → hand/battlefield** com ChangeType creature/land/card/any/vazio. Reorder library→library não é tutor.
14. **SVar strip no Final.** `build_model_facts` remove effects kind=svar da vista Final. Candidates (e o índice) usam o mapping sobre o record cru, não o Final.
15. **Layouts sem Oracle jogável** (`art_series`, `token`, `emblem`, `planar`, `scheme`, `vanguard`, …) ficam unmatched de propósito.
16. **`--reapply-mapping` sem remine.** YAML novo → `enrich_ontology.py --reapply-mapping` → `rebuild_predicate_index`. Este inventário assume o reapply `2026-09-02T04:57:58Z` (mapping 1.2.0).

---

## Apêndice: o que o model_config faz (e não faz) aos factos finais

`model_config_v1.json` escolhe fonte por campo (Scryfall para nome/oracle/identity; Forge para effects/costs/triggers/deck_*; resolved para types/symbols/ability_kinds). `value_filters` está vazio. Isso muda o JSON do validador, **não** o conjunto de predicados.

`flatten_candidates` ignora `validation_only`, serializa valores compostos com `json.dumps`, e **não** indexa `token_script`, `color` fora de {W,U,B,R,G,C,any}, nem `rate` não-numérico.
