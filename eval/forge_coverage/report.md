# Auditoria de cobertura do Forge (Fase 1)

Gerado por `scripts/audit_forge_coverage.py` contra `src/operators/algebra.py`.
Plano: [`zuilho_plans/economia-de-operadores.md`](../../zuilho_plans/economia-de-operadores.md).

## Resultado

- Corpus: Forge `09eac29911cdbfea1529ea19b12f0ae076a3369a` (sem filtro de legalidade: catálogo Scryfall ausente)
- Cartas auditadas: **33369** (excluídas: 475; erros de parse: 0)
- Abstraíveis (nenhum elemento OUT/UNKNOWN): **31132** = **93.3%**
- Cobertura estrita (sem `Effect`/`ReplaceEffect` opacos): **88.2%**
- Critério de aceite ≥ 80%: **passou** (parada abaixo de 60%)
- Efeitos de recurso: 46594; com magnitude constante: **88.7%** (o resto depende de X / contagem do estado)

Nível 1 (scriptada) e nível 2 (abstraível) estão medidos aqui. O nível 3
(fiel: o operador prevê o Δ medido no Forge) é a Fase 3.

**Leitura honesta:** esta é cobertura *por tipo de elemento*. Ela diz que todo
elemento da carta tem lugar na álgebra, não que os parâmetros dele já viram um
operador correto. É condição necessária para a Fase 2, não suficiente; o teste
de suficiência é a fidelidade da Fase 3. Palavras-chave sem classe explícita
contam como MODIFIER por padrão, e `Effect` esconde estáticas internas — por
isso a métrica estrita.

## Vocabulário

| Tipo | Valores distintos |
|---|---|
| api | 189 |
| keyword | 245 |
| replacement | 34 |
| static | 97 |
| trigger | 130 |

Elementos sem classe (UNKNOWN), a revisar na álgebra:

_nenhum_

## Famílias fora da álgebra (OUT), por nº de cartas

| Elemento | Cartas |
|---|---|
| `api:CopyPermanent` | 343 |
| `api:GainControl` | 310 |
| `api:SetState` | 278 |
| `api:CopySpellAbility` | 236 |
| `api:Clone` | 164 |
| `api:AlterAttribute` | 123 |
| `static:Continuous[RemoveAllAbilities]` | 49 |
| `api:RingTemptsYou` | 49 |
| `keyword:StickerSheet` | 48 |
| `keyword:Start your engines` | 46 |
| `api:ChangeTargets` | 43 |
| `static:Continuous[GainControl]` | 42 |
| `api:Draft` | 42 |
| `static:Continuous[SetColor]` | 40 |
| `api:Venture` | 37 |
| `api:PutSticker` | 35 |
| `keyword:Mutate` | 34 |
| `api:Vote` | 33 |
| `trigger:Mutates` | 32 |
| `api:ExchangeControl` | 32 |
| `api:TwoPiles` | 30 |
| `static:Continuous[RemoveType]` | 28 |
| `keyword:Banding` | 24 |
| `keyword:Myriad` | 23 |
| `api:OpenAttraction` | 21 |
| `api:AssembleContraption` | 20 |
| `keyword:Specialize` | 19 |
| `api:DayTime` | 14 |
| `api:VillainousChoice` | 13 |
| `api:ChangeText` | 12 |

## Cartas que mais quebram o modelo

| Carta | OUT | UNKNOWN | Elementos |
|---|---|---|---|
| Gut, Fanatical Priestess | 11 | 0 | `api:CopyPermanent`, `keyword:Specialize`, `trigger:Specializes` |
| Karlach, Raging Tiefling | 11 | 0 | `keyword:Specialize`, `trigger:Specializes` |
| Lukamina, Moon Druid | 8 | 0 | `api:SetState`, `keyword:Specialize`, `trigger:Specializes` |
| Wyll, Pact-Bound Duelist | 7 | 0 | `api:GainControl`, `keyword:Specialize`, `trigger:Specializes` |
| Jaheira, Harper Emissary | 6 | 0 | `keyword:Specialize`, `trigger:Specializes` |
| Lae'zel, Githyanki Warrior | 6 | 0 | `keyword:Specialize`, `trigger:Specializes` |
| Vhal, Eager Scholar | 6 | 0 | `keyword:Specialize`, `trigger:Specializes` |
| Viconia, Nightsinger's Disciple | 6 | 0 | `keyword:Specialize`, `trigger:Specializes` |
| Klement, Novice Acolyte | 4 | 0 | `keyword:Specialize`, `trigger:Specializes` |
| Chef's Kiss | 3 | 0 | `api:ChangeTargets`, `api:ControlSpell`, `api:CopySpellAbility` |
| Corrupted Shapeshifter | 3 | 0 | `api:Clone` |
| Frantic Scapegoat | 3 | 0 | `api:AlterAttribute` |
| Geier Reach Bandit | 3 | 0 | `api:SetState` |
| Grub, Storied Matriarch | 3 | 0 | `api:CopyPermanent`, `api:SetState` |
| Hostile Negotiations | 3 | 0 | `api:SetState`, `api:TwoPiles` |
| Lithoform Engine | 3 | 0 | `api:CopySpellAbility` |
| Path of the Animist | 3 | 0 | `api:ChaosEnsues`, `api:Planeswalk`, `api:Vote` |
| Path of the Enigma | 3 | 0 | `api:ChaosEnsues`, `api:Planeswalk`, `api:Vote` |
| Path of the Ghosthunter | 3 | 0 | `api:ChaosEnsues`, `api:Planeswalk`, `api:Vote` |
| Path of the Pyromancer | 3 | 0 | `api:ChaosEnsues`, `api:Planeswalk`, `api:Vote` |
| Path of the Schemer | 3 | 0 | `api:ChaosEnsues`, `api:Planeswalk`, `api:Vote` |
| Poppet Stitcher | 3 | 0 | `api:SetState`, `static:Continuous[RemoveAllAbilities]` |
| Primal Clay | 3 | 0 | `api:Clone` |
| Primal Plasma | 3 | 0 | `api:Clone` |
| Shifting Grift | 3 | 0 | `api:ExchangeControl` |

## Vocabulário completo (top 60 por frequência)

| Tipo | Valor | Ocorrências | Classe |
|---|---|---|---|
| trigger | `ChangesZone` | 7559 | event |
| api | `ChangeZone` | 6504 | resource |
| api | `Pump` | 4954 | resource |
| static | `Continuous` | 4555 | modifier |
| api | `Draw` | 3692 | resource |
| api | `Token` | 3473 | resource |
| keyword | `Flying` | 3306 | modifier |
| api | `PutCounter` | 3295 | resource |
| api | `Cleanup` | 2930 | plumbing |
| api | `DealDamage` | 2844 | resource |
| api | `Mana` | 2492 | resource |
| trigger | `Phase` | 2277 | event |
| api | `Effect` | 1850 | modifier |
| api | `GainLife` | 1770 | resource |
| trigger | `Attacks` | 1586 | event |
| api | `Destroy` | 1540 | resource |
| api | `Tap` | 1485 | resource |
| trigger | `SpellCast` | 1385 | event |
| keyword | `Enchant` | 1257 | modifier |
| api | `LoseLife` | 1192 | resource |
| api | `Discard` | 1089 | resource |
| keyword | `Trample` | 1039 | modifier |
| api | `PumpAll` | 1036 | resource |
| api | `Animate` | 1002 | resource |
| replacement | `Moved` | 960 | modifier |
| api | `Dig` | 956 | resource |
| api | `Sacrifice` | 873 | resource |
| trigger | `DamageDone` | 859 | event |
| api | `Charm` | 796 | plumbing |
| keyword | `Vigilance` | 754 | modifier |
| keyword | `Haste` | 684 | modifier |
| api | `ChangeZoneAll` | 658 | resource |
| keyword | `Equip` | 645 | modifier |
| keyword | `Flash` | 632 | modifier |
| api | `Mill` | 582 | resource |
| api | `Counter` | 536 | resource |
| static | `ReduceCost` | 516 | modifier |
| api | `Untap` | 485 | resource |
| keyword | `etbCounter` | 473 | modifier |
| api | `DelayedTrigger` | 471 | modifier |
| keyword | `Reach` | 445 | modifier |
| api | `Scry` | 438 | resource |
| api | `DamageAll` | 422 | resource |
| api | `ChooseCard` | 420 | plumbing |
| keyword | `ETBReplacement` | 418 | modifier |
| keyword | `Menace` | 415 | modifier |
| keyword | `First Strike` | 392 | modifier |
| keyword | `Lifelink` | 389 | modifier |
| api | `DestroyAll` | 361 | resource |
| api | `CopyPermanent` | 359 | out |
| static | `CantBlockBy` | 359 | mask |
| keyword | `Deathtouch` | 351 | modifier |
| api | `SetState` | 346 | out |
| api | `RepeatEach` | 335 | plumbing |
| api | `ImmediateTrigger` | 324 | modifier |
| api | `GainControl` | 322 | out |
| api | `Play` | 316 | resource |
| keyword | `Defender` | 311 | modifier |
| keyword | `Cycling` | 306 | modifier |
| api | `PutCounterAll` | 298 | resource |
