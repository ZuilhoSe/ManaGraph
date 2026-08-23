"""Backward-compatible facade over deck_analysis/*.

mana.py's logic was split by responsibility into src/deck_analysis/
(mana_symbols, card_classify, curve, mana_base, diagnose, shape_bonus). This
module re-exports the same public names so existing imports (`from mana
import diagnose_deck`, etc.) keep working unchanged -- no other file had to
change in this pass.
"""

from deck_analysis.card_classify import is_cheat_card, is_fast_commander, is_ramp_commander
from deck_analysis.curve import (
    AVG_CMC_BAND,
    CURVE_PROFILES,
    CURVE_TARGETS,
    cmc_bucket,
    curve_plan,
    select_curve_profile,
)
from deck_analysis.diagnose import diagnose, diagnose_deck
from deck_analysis.mana_base import (
    LAND_SEVERITY_BANDS,
    LAND_SEVERITY_SCALE,
    MIN_SOURCES,
    PIP_PER_SOURCE_WARN,
    color_floors,
    land_alert,
    min_sources_for,
)
from deck_analysis.mana_symbols import (
    ADD_ANY_RE,
    ADD_BRACES_RE,
    COLORS,
    LAND_TYPES,
    SYMBOL_RE,
    TREASURE_RE,
    WORD_NUM,
    empty_pips,
    empty_sources,
    is_fast_mana,
    is_land_card,
    parse_mana_cost,
    produced_mana,
    produces_mana,
)
from deck_analysis.shape_bonus import shape_bonus
from deck_analysis.strategies import (
    HypergeometricStrategy,
    ManaTargetStrategy,
    StaticQuotaStrategy,
    strategy_from_name,
)

__all__ = [
    "COLORS",
    "LAND_TYPES",
    "WORD_NUM",
    "SYMBOL_RE",
    "ADD_BRACES_RE",
    "ADD_ANY_RE",
    "TREASURE_RE",
    "CURVE_PROFILES",
    "CURVE_TARGETS",
    "AVG_CMC_BAND",
    "PIP_PER_SOURCE_WARN",
    "MIN_SOURCES",
    "LAND_SEVERITY_BANDS",
    "LAND_SEVERITY_SCALE",
    "color_floors",
    "land_alert",
    "cmc_bucket",
    "empty_pips",
    "empty_sources",
    "parse_mana_cost",
    "produced_mana",
    "produces_mana",
    "is_land_card",
    "is_fast_mana",
    "min_sources_for",
    "is_cheat_card",
    "is_fast_commander",
    "is_ramp_commander",
    "select_curve_profile",
    "curve_plan",
    "diagnose",
    "diagnose_deck",
    "shape_bonus",
    "ManaTargetStrategy",
    "StaticQuotaStrategy",
    "HypergeometricStrategy",
    "strategy_from_name",
]
