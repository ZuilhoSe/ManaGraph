"""Operator algebra (zuilho_plans/economia-de-operadores.md, seção 2).

Every rules element a Forge card script can carry is classified into one of
five classes. Phase 1 uses the table to measure how much of the card pool the
algebra can express; Phase 2 compiles cards into operators along the same
classes.

    RESOURCE   1st order: changes the resource vector (mana, cards, bodies,
               counters, life, damage, removal, tutoring, ...).
    EVENT      an event other cards can subscribe to (trigger modes).
    MODIFIER   2nd order: changes other operators (cost reducers, doublers,
               ETB replacements, anthem/grant statics, extra triggers).
    MASK       rule masks: "can't" / "must" restrictions, terminal-set
               changes (can't lose, can't win, win/lose the game).
    PLUMBING   control flow with no game meaning of its own (choices,
               cleanup, repeat, branch, store variable). Transparent.
    OUT        outside the algebra: copy/clone/layer and text changes,
               control exchange, subgames, Un-/Planechase/Attractions, ...

Anything not in these tables is UNKNOWN and is counted against coverage, so
the table can only be made more generous on purpose.
"""

from __future__ import annotations

RESOURCE = "resource"
EVENT = "event"
MODIFIER = "modifier"
MASK = "mask"
PLUMBING = "plumbing"
OUT = "out"
UNKNOWN = "unknown"

IN_ALGEBRA = frozenset({RESOURCE, EVENT, MODIFIER, MASK, PLUMBING})

# Resources of the abstract state (seção 2.1). Used by Phase 2 compilation.
RESOURCES = (
    "mana", "card", "body", "token", "counter", "life", "damage", "poison",
    "permanent_removed", "spell_countered", "library_search", "graveyard_return",
    "tap_state", "selection", "extra_turn", "extra_combat", "monarch",
)

# --- effect APIs (A:/SVar: "AB$ X", "SP$ X", "DB$ X") ------------------------
API_CLASS: dict[str, str] = {}

for _api in (
    "ChangeZone", "ChangeZoneAll", "Pump", "PumpAll", "Draw", "Token", "PutCounter",
    "PutCounterAll", "DealDamage", "DamageAll", "EachDamage", "Mana", "ManaReflected",
    "GainLife", "LoseLife", "SetLife", "Destroy", "DestroyAll", "Tap", "TapAll", "Untap",
    "UntapAll", "TapOrUntap", "TapOrUntapAll", "TaporUntapAll", "Discard", "Dig",
    "DigUntil", "DigMultiple", "Sacrifice", "SacrificeAll", "Mill", "Counter", "Scry",
    "Surveil", "RemoveCounter", "RemoveCounterAll", "MultiplyCounter", "MoveCounter",
    "AddOrRemoveCounter", "Regenerate", "Regeneration", "Fight", "Investigate",
    "Proliferate", "Amass", "Connive", "Explore", "Incubate", "Discover", "Poison",
    "AddTurn", "AddPhase", "BecomeMonarch", "TakeInitiative", "Animate", "AnimateAll",
    "Attach", "Unattach", "Play", "MakeCard", "Seek", "PreventDamage", "Fog",
    "Debuff", "Protection", "ProtectionAll", "Goad", "Detain", "Manifest",
    "ManifestDread", "Cloak", "Learn", "Endure", "Earthbend", "Airbend", "Empower",
    "Blight", "Radiation", "Recruit", "RevealHand", "LookAt", "RearrangeTopOfLibrary",
    "Clash", "RollDice", "FlipCoin", "DrainMana", "HealDamage", "Abandon", "Intensify",
    "PermanentCreature", "PermanentNoncreature", "ExchangeLife", "ExchangeLifeVariant",
    "ExchangePower", "UnlockDoor", "Phases", "RemoveFromCombat", "MustBlock", "Block",
    "BecomesBlocked", "ChangeCombatants", "Balance", "TimeTravel", "Heist", "DamageResolve",
):
    API_CLASS[_api] = RESOURCE

for _api in ("Effect", "ReplaceEffect", "ReplaceDamage", "ReplaceToken", "ReplaceCounter",
             "ReplaceMana", "ReplaceSplitDamage", "DelayedTrigger", "ImmediateTrigger"):
    API_CLASS[_api] = MODIFIER

for _api in ("LosesGame", "WinsGame", "GameDrawn", "SkipPhase", "SkipTurn", "EndTurn",
             "EndCombatPhase"):
    API_CLASS[_api] = MASK

for _api in (
    "Cleanup", "Charm", "RepeatEach", "Repeat", "Branch", "StoreSVar", "ChooseCard",
    "ChooseType", "ChooseColor", "ChoosePlayer", "ChooseNumber", "ChooseSource",
    "ChooseEvenOdd", "ChooseDirection", "ChooseSector", "GenericChoice", "Reveal",
    "PeekAndReveal", "Shuffle", "NameCard", "BlankLine", "ReorderZone", "AssignGroup",
    "ChangeX",
):
    API_CLASS[_api] = PLUMBING

for _api in (
    "CopyPermanent", "Clone", "CopySpellAbility", "GainControl", "GainControlVariant",
    "ExchangeControl", "ExchangeControlVariant", "ControlPlayer", "ControlSpell",
    "ChangeText", "ExchangeTextBox", "SetState", "AlterAttribute", "ChangeTargets",
    "Subgame", "Planeswalk", "RollPlanarDice", "Draft", "OpenAttraction",
    "AssembleContraption", "AdvanceCrank", "PutSticker", "Venture", "RingTemptsYou",
    "DayTime", "ChaosEnsues", "RunChaos", "Vote", "VillainousChoice", "TwoPiles",
    "MultiplePiles", "BidLife", "Meld", "ReverseTurnOrder", "RestartGame",
    "RemoveFromMatch", "RemoveFromGame", "SetInMotion", "ClaimThePrize", "ExchangeZone",
    "GainOwnership", "LosePerpetual", "ChangeSpeed", "SwitchBlock", "ActivateAbility",
    "FlipOntoBattlefield", "Camouflage",
):
    API_CLASS[_api] = OUT

# --- trigger modes (T: Mode$ X) ---------------------------------------------
TRIGGER_CLASS: dict[str, str] = {}
for _mode in (
    "ChangesZone", "ChangesZoneAll", "Phase", "Attacks", "SpellCast", "SpellCastOrCopy",
    "DamageDone", "DamageDoneOnce", "DamageDealtOnce", "DamageAll", "AttackersDeclared",
    "AttackersDeclaredOneTarget", "Drawn", "Blocks", "AttackerBlocked",
    "AttackerBlockedByCreature", "AttackerBlockedOnce", "AttackerUnblocked",
    "AttackerUnblockedOnce", "BlockersDeclared", "TurnFaceUp", "BecomesTarget",
    "BecomesTargetOnce", "Sacrificed", "SacrificedOnce", "Taps", "TapAll", "Untaps",
    "UntapAll", "LifeGained", "LifeLost", "LifeLostAll", "Discarded", "DiscardedAll",
    "Cycled", "TapsForMana", "ManaAdded", "ManaExpend", "Always", "CounterAdded",
    "CounterAddedOnce", "CounterAddedAll", "CounterTypeAddedAll", "CounterPlayerAddedAll",
    "CounterRemoved", "CounterRemovedOnce", "AbilityCast", "SpellAbilityCast", "LandPlayed",
    "Transformed", "Exploited", "Scry", "Surveil", "CommitCrime", "BecomeMonstrous",
    "BecomeRenowned", "Attached", "Unattached", "TurnBegin", "Proliferate", "Crewed",
    "BecomesCrewed", "Explores", "SpellCopy", "SpellAbilityCopy", "Exerted", "Countered",
    "SearchedLibrary", "Shuffled", "BecomeMonarch", "AbilityTriggered", "AbilityResolves",
    "Enlisted", "Connives", "Destroyed", "TokenCreated", "TokenCreatedOnce", "Milled",
    "MilledOnce", "MilledAll", "Exiled", "ExcessDamage", "ExcessDamageAll", "Evolved",
    "Discover", "Investigated", "PayLife", "PayCumulativeUpkeep", "PayEcho", "Fight",
    "FightOnce", "Devoured", "Championed", "Mentored", "Trains", "Saddled",
    "BecomesSaddled", "CollectEvidence", "Forage", "CaseSolved", "UnlockDoor",
    "FullyUnlock", "RolledDie", "RolledDieOnce", "FlippedCoin", "FlippedCoinOnce",
    "Clashed", "LosesGame", "DamagePreventedOnce", "Foretell", "BecomesPlotted",
    "Stationed", "ElementalBend", "ManifestDread", "Abandoned", "GiveGift",
    "ChangesController", "Cycled", "PhaseIn", "PhaseOut", "PhaseOutAll", "NewGame",
):
    TRIGGER_CLASS[_mode] = EVENT
for _mode in (
    "PlaneswalkedTo", "PlaneswalkedFrom", "PlanarDice", "SetInMotion", "ChaosEnsues",
    "CrankContraption", "VisitAttraction", "ClaimPrize", "StickerPlaced", "RingTemptsYou",
    "Mutates", "Specializes", "DayTimeChanges", "DungeonCompleted", "Vote", "FacesDilemma",
    "ConjureAll", "SeekAll",
):
    TRIGGER_CLASS[_mode] = OUT

# --- static modes (S: Mode$ X; comma lists are split) -----------------------
STATIC_CLASS: dict[str, str] = {}
for _mode in (
    "Continuous", "ReduceCost", "RaiseCost", "SetCost", "AlternativeCost", "OptionalCost",
    "OptionalAttackCost", "CastWithFlash", "Panharmonicon", "ManaConvert", "UnspentMana",
    "ManaRestriction", "CombatDamageToughness", "AssignCombatDamageAsUnblocked",
    "UntapOtherPlayer", "TapPowerValue", "ActivateAbilityAsIfHaste", "CanAttackIfHaste",
    "CanAttackDefender", "CanBlockIfShadow", "CanBlockIfReach", "IgnoreLandWalk",
    "IgnoreHexproof", "IgnoreLegendRule", "NoCleanupDamage", "CountersRemain",
    "NumLoyaltyAct", "Activations", "DrawFromBottom", "BlockTapped", "InfectDamage",
    "WitherDamage", "ColorlessDamageSource", "SurveilNum", "FlipCoinDoubler", "FlipCoinMod",
    "Devotion", "MaxCounter", "GainLifeRadiation", "PlotZone", "CombatDamageNegatePower",
    "IgnorePlaneswalkerZeroLoyaltyRule", "ManaBurn", "MinMaxBlocker",
):
    STATIC_CLASS[_mode] = MODIFIER
for _mode in (
    "CantBlockBy", "CantBlock", "CantAttack", "CantBeCast", "MustAttack", "CantBeActivated",
    "CantTarget", "CantAttackUnless", "CantGainLife", "CantPreventDamage", "CantPlayLand",
    "CantPutCounter", "CantSacrifice", "CantBlockUnless", "DisableTriggers", "MustBlock",
    "AttackRestrict", "CantDraw", "CantAttach", "AttackRequirement", "BlockRestrict",
    "CantPayLife", "MustTarget", "CantBeCopied", "PlayerMustAttack", "CantTransform",
    "CantDiscard", "CantExile", "CantVenture", "CantChangeLife", "CantGainControl",
    "CantBeSuspected", "CantChangeDayTime", "CantPhaseIn", "CantCrew", "CantBeBeamedUp",
):
    STATIC_CLASS[_mode] = MASK
for _mode in ("PhaseReversed", "TurnReversed"):
    STATIC_CLASS[_mode] = OUT

# Continuous statics are MODIFIER (anthems, keyword grants, cost of being an
# Aura/Equipment) unless they rewrite characteristics in rules layers 1-4 or
# strip abilities: those are OUT, like Clone/GainControl on the API side.
CONTINUOUS_OUT_PARAMS = frozenset({
    "RemoveAllAbilities", "RemoveType", "RemoveCardTypes", "RemoveSubTypes",
    "RemoveCreatureTypes", "SetColor", "GainControl", "ChangeColorWordsTo", "GainTextOf",
    "SetName",
})

# --- replacement events (R: Event$ X) ---------------------------------------
REPLACEMENT_CLASS: dict[str, str] = {}
for _event in (
    "Moved", "DamageDone", "Untap", "Counter", "Draw", "DrawCards", "AddCounter",
    "CreateToken", "GainLife", "BeginPhase", "ProduceMana", "TurnFaceUp", "LifeReduced",
    "Destroy", "BeginTurn", "LoseMana", "Mill", "Explore", "Scry", "RemoveCounter",
    "Proliferate", "CopySpell", "Connive", "PayLife", "Cascade", "Learn", "Transform",
    "Attached", "RollDice",
):
    REPLACEMENT_CLASS[_event] = MODIFIER
for _event in ("GameLoss", "GameWin"):
    REPLACEMENT_CLASS[_event] = MASK
for _event in ("Planeswalk", "RollPlanarDice", "AssembleContraption"):
    REPLACEMENT_CLASS[_event] = OUT

# --- keywords (K: X) ---------------------------------------------------------
# Most keywords are evasion/combat statics (MODIFIER) or alternative costs. Only
# the ones that change identity, copy, or live outside a normal game are OUT.
OUT_KEYWORDS = frozenset({
    "Banding", "Bands with other", "Mutate", "StickerSheet", "Myriad",
    "Hidden agenda", "Double agenda", "Draft", "Specialize", "Visit", "Read ahead",
    "Start your engines",
})

# Non-traditional card types the audit excludes from the Commander pool.
NON_TRADITIONAL_TYPES = frozenset({
    "Plane", "Phenomenon", "Scheme", "Vanguard", "Conspiracy", "Dungeon", "Attraction",
    "Contraption",
})


def static_value(mode: str, params: dict) -> str:
    """Refined label for a static: ``Continuous`` gets a ``[Param]`` suffix when it is OUT."""
    if "Continuous" in mode.split(","):
        hits = sorted(k for k in params if k in CONTINUOUS_OUT_PARAMS)
        if hits:
            return f"Continuous[{hits[0]}]"
    return mode


def classify(kind: str, value: str) -> str:
    """Class of one rules element. ``kind`` is api | trigger | static | replacement | keyword."""
    if kind == "api":
        return API_CLASS.get(value, UNKNOWN)
    if kind == "trigger":
        return TRIGGER_CLASS.get(value, UNKNOWN)
    if kind == "static":
        if value.startswith("Continuous["):
            return OUT
        parts = [p.strip() for p in value.split(",") if p.strip()]
        classes = {STATIC_CLASS.get(p, UNKNOWN) for p in parts} or {UNKNOWN}
        for worst in (OUT, UNKNOWN):
            if worst in classes:
                return worst
        return MASK if MASK in classes else MODIFIER
    if kind == "replacement":
        return REPLACEMENT_CLASS.get(value, UNKNOWN)
    if kind == "keyword":
        return OUT if value in OUT_KEYWORDS else MODIFIER
    return UNKNOWN
