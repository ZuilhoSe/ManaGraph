# Dominância entre cartas (Fase 2)

Gerado por `scripts/build_dominance.py` a partir dos scripts do Forge.
A ≥ B: mesma assinatura (tipos + estrutura de operadores) e A é pelo menos
tão boa em custo, velocidade, corpo, palavras-chave, condições, alvo e magnitude.
Nenhum sinal de popularidade ou preço.

## Números

| | |
|---|---:|
| cartas legais compiladas | 31699 |
| cartas com parâmetro genérico não lido (fora da comparação) | 169 |
| relações de dominância estrita | 2291 |
| … com os mesmos subtipos | 733 |
| cartas dominadas por pelo menos uma outra | 920 |
| pares equivalentes (reimpressões funcionais) | 701 |

## Pares canônicos (aceite)

| par | esperado | obtido | ok |
|---|---|---|---|
| Lightning Bolt × Shock | Lightning Bolt > Shock | Lightning Bolt > Shock | ✅ |
| Counterspell × Cancel | Counterspell > Cancel | Counterspell > Cancel | ✅ |
| Murder × Doom Blade | incomparáveis | incomparáveis | ✅ |
| Counterspell × Mana Leak | incomparáveis | incomparáveis | ✅ |
| Naturalize × Disenchant | incomparáveis | incomparáveis | ✅ |
| Llanowar Elves × Grizzly Bears | incomparáveis | incomparáveis | ✅ |

**Pares canônicos: todos corretos.**

## Amostra para conferência manual (100 relações, semente 7)

Aceite da Fase 2: conferir à mão. Marque as erradas e o motivo.

| # | dominador | custo | dominado | custo | subtipos diferem |
|---:|---|---|---|---|---|
| 1 | Walking Corpse | 1 B | Wei Infantry | 1 B | sim |
| 2 | Yotian Medic | 2 W | Femeref Scouts | 2 W | sim |
| 3 | Flame Lash | 3 R | Electrify | 3 R |  |
| 4 | Greenwood Sentinel | 1 G | Barbary Apes | 1 G | sim |
| 5 | Brazen Scourge | 1 R R | Talruum Minotaur | 2 R R | sim |
| 6 | Feisty Spikeling | 1 RW | Fresh-Faced Recruit | 1 RW | sim |
| 7 | Magnigoth Sentry | 3 G | Cloudcrown Oak | 2 G G | sim |
| 8 | Alloy Myr | 3 | Opaline Unicorn | 3 | sim |
| 9 | Bishop's Soldier | 1 W | Glory Seeker | 1 W | sim |
| 10 | Rumble Arena | no cost | Crystal Grotto | no cost |  |
| 11 | Hired Blade | 2 B | Warpath Ghoul | 2 B | sim |
| 12 | Ashcoat Bear | 1 G | Scarwood Goblins | R G | sim |
| 13 | Capital Guard | 1 R | Raging Bull | 2 R | sim |
| 14 | Predator's Strike | 1 G | Awaken the Bear | 2 G |  |
| 15 | Repel Calamity | 1 W | Collar the Culprit | 3 W |  |
| 16 | Brambleweft Behemoth | 4 G G | Cowl Prowler | 4 G G | sim |
| 17 | Skysnare Spider | 4 G G | Kindercatch | 3 G G G | sim |
| 18 | Charging Monstrosaur | 4 R | Renegade Troops | 4 R | sim |
| 19 | Vitalize | G | Mobilize | G |  |
| 20 | Finishing Blow | 4 B | Reach of Shadows | 4 B |  |
| 21 | Bishop's Soldier | 1 W | Traveling Philosopher | 1 W | sim |
| 22 | Dragon Moose | 3 R | Lagac Lizard | 3 R | sim |
| 23 | Riot Devils | 2 R | Hurloon Minotaur | 1 R R | sim |
| 24 | Oko's Accomplices | 2 U | Blind Phantasm | 2 U | sim |
| 25 | Thundering Rebuke | 1 R | Explosive Shot | 1 R |  |
| 26 | Barbtooth Wurm | 5 G | Craw Wurm | 4 G G |  |
| 27 | Hulking Devil | 3 R | Incurable Ogre | 3 R | sim |
| 28 | Elephant-Rat | 1 B | Bane Alley Blackguard | 1 B | sim |
| 29 | Reckless Spite | 1 B B | Wicked Pact | 1 B B |  |
| 30 | Warship Scout | R | Dwarven Trader | R | sim |
| 31 | Raging Cougar | 2 R | Raging Bull | 2 R | sim |
| 32 | Volcanic Upheaval | 3 R | Craterize | 3 R |  |
| 33 | Falkenrath Reaver | 1 R | Goblin Bully | 1 R | sim |
| 34 | Vryn Wingmare | 2 W | Glowrider | 2 W | sim |
| 35 | Deadly Recluse | 1 G | Underdark Basilisk | 1 G | sim |
| 36 | Silvercoat Lion | 1 W | Squire | 1 W | sim |
| 37 | Harrier Naga | 2 G | Gnarled Mass | 1 G G | sim |
| 38 | Umara Entangler | 1 U | Coral Eel | 1 U | sim |
| 39 | Goblin Roughrider | 2 R | Goblin Hero | 2 R | sim |
| 40 | Sea Gate Banneret | W | Cliffside Lookout | W | sim |
| 41 | Colossal Dreadmaw | 4 G G | Craw Wurm | 4 G G | sim |
| 42 | Inspiring Overseer | 2 W | Priest of Ancient Lore | 2 W | sim |
| 43 | Blood Glutton | 4 B | Zombie Goliath | 4 B | sim |
| 44 | Bitterbow Sharpshooters | 4 G | Plated Spider | 4 G | sim |
| 45 | Hand of Silumgar | 1 B | Skeletal Snake | 1 B | sim |
| 46 | Stunt Double | 3 U | Clone | 3 U |  |
| 47 | Strip Mine | no cost | Tectonic Edge | no cost |  |
| 48 | Sudden Strike | 1 W | Kill Shot | 2 W |  |
| 49 | Traveling Philosopher | 1 W | Squire | 1 W | sim |
| 50 | Blooming Marsh | no cost | Golgari Guildgate | no cost | sim |
| 51 | Dragonskull Summit | no cost | Rakdos Guildgate | no cost | sim |
| 52 | Gideon's Lawkeeper | W | Akroan Jailer | W |  |
| 53 | Woodland Changeling | 1 G | Scarwood Goblins | R G | sim |
| 54 | Rustwing Falcon | W | Lantern Kami | W | sim |
| 55 | Glory Seeker | 1 W | Squire | 1 W |  |
| 56 | Knight of the Keep | 2 W | Pearled Unicorn | 2 W | sim |
| 57 | Cowl Prowler | 4 G G | Canopy Gorger | 4 G G |  |
| 58 | Vampire Noble | 2 B | Scathe Zombies | 2 B | sim |
| 59 | Ritual of Rejuvenation | 2 W | Reviving Dose | 2 W |  |
| 60 | Cinder Glade | no cost | Highland Forest | no cost |  |
| 61 | Aerial Maneuver | 1 W | Daring Leap | 1 W U |  |
| 62 | Befuddle | 2 U | Bewilder | 2 U |  |
| 63 | Vampire Noble | 2 B | Python | 1 B B | sim |
| 64 | Breakneck Berserker | 2 R | Goblin Chariot | 2 R | sim |
| 65 | Vampire Nighthawk | 1 B B | Deathgaze Cockatrice | 2 B B | sim |
| 66 | Drowned Catacomb | no cost | Frost Marsh | no cost |  |
| 67 | Humble Budoka | 1 G | Forest Bear | 1 G | sim |
| 68 | Springjaw Trap | 1 | Silent Dart | 1 |  |
| 69 | Feral Maaka | 1 R | Goblin Hero | 2 R | sim |
| 70 | Manalith | 3 | Celestial Prism | 3 |  |
| 71 | Kill Shot | 2 W | Eightfold Maze | 2 W |  |
| 72 | Aven Squire | 1 W | Royal Falcon | 1 W | sim |
| 73 | Brightblade Stoat | 1 W | Story Seeker | 1 W | sim |
| 74 | Saber-Tooth Moose-Lion | 4 G G | Wirewood Guardian | 5 G G | sim |
| 75 | Territorial Roc | 1 W | Squire | 1 W | sim |
| 76 | Aeolipile | 2 | Vial of Dragonfire | 2 |  |
| 77 | Air Response Unit | 2 W | Dragonfly Suit | 2 W |  |
| 78 | Dukhara Peafowl | 4 | Cobalt Golem | 4 | sim |
| 79 | Poison the Blade | 1 G | Lace with Moonglove | 2 G |  |
| 80 | Bounding Wolf | 2 G | Gorilla Warrior | 2 G | sim |
| 81 | Phantom Monster | 3 U | Cloud Manta | 3 U | sim |
| 82 | Squirrelanoids | B | Muck Rats | B | sim |
| 83 | Inspiring Vantage | no cost | Rabanastre, Royal City | no cost | sim |
| 84 | Boggart Brute | 2 R | Viashino Runner | 3 R | sim |
| 85 | Blanchwood Treefolk | 4 G | Durkwood Boars | 4 G | sim |
| 86 | Skyblade of the Legion | 1 W | Squire | 1 W | sim |
| 87 | Orcish Vandal | 1 R | Orcish Mechanics | 2 R | sim |
| 88 | Pensive Minotaur | 2 R | Raging Bull | 2 R | sim |
| 89 | Voyaging Satyr | 1 G | Ley Druid | 2 G | sim |
| 90 | Bite Down | 1 G | Aggressive Instinct | 1 G |  |
| 91 | Vulpine Goliath | 4 G G | Alpha Tyrranax | 4 G G | sim |
| 92 | Bot Bashing Time | 3 R | Reduce to Ashes | 4 R |  |
| 93 | Inspiring Cleric | 2 W | Lagonna-Band Elder | 2 W | sim |
| 94 | Frenzied Raptor | 2 R | Viashino Warrior | 3 R | sim |
| 95 | Dawn's Light Archer | 2 G | Orazca Frillback | 2 G | sim |
| 96 | Warrior's Honor | 2 W | Warrior's Charge | 2 W |  |
| 97 | Bishop's Soldier | 1 W | Squire | 1 W | sim |
| 98 | Hostile Minotaur | 3 R | Wild Jhovall | 3 R | sim |
| 99 | Vorstclaw | 4 G G | Primordial Wurm | 4 G G | sim |
| 100 | Ordinary Bear | 3 G | Durkwood Boars | 4 G | sim |

## Invólucros genéricos ainda não lidos

| átomo | cartas |
|---|---:|
| `api:Animate` | 124 |
| `api:AnimateAll` | 39 |
| `replacement:Moved` | 5 |
| `api:Play` | 2 |
| `static:Continuous.MayPlay` | 1 |
