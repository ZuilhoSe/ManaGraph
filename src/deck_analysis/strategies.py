"""Pluggable mana-target strategies: same input/output contract, different math.

Mirrors the EmbeddingStrategy/MiniLMStrategy pattern in embeddings.py -- a base
class plus concrete providers, selected at call time by the caller of diagnose().
"""

from __future__ import annotations

from hypergeometric import color_pip_share, recommended_color_sources, recommended_land_count

from deck_analysis.mana_base import band_alert, color_floors, land_alert
from deck_analysis.mana_symbols import COLORS

# Land quota band around the hypergeometric target: tighter than the +-2 tolerance
# used when validating recommended_land_count itself against real decklists (see
# hypergeometric.py), since the adapter's own band is meant to bite sooner.
HYPERGEOMETRIC_LAND_BAND = 1


class ManaTargetStrategy:
    def land_target(self, land_count: int, curve_by_cmc: dict[int, int] | None) -> dict:
        raise NotImplementedError

    def color_floors(
        self,
        identity: list[str] | None,
        pip_reqs_by_color: dict[str, list[tuple[int, int]]] | None,
    ) -> dict[str, int]:
        raise NotImplementedError


class StaticQuotaStrategy(ManaTargetStrategy):
    """Today's shipped heuristic: a fixed ROLE_QUOTAS['land'] band and a per-color-count floor."""

    def land_target(self, land_count: int, curve_by_cmc: dict[int, int] | None) -> dict:
        return land_alert(land_count, curve_by_cmc)

    def color_floors(
        self,
        identity: list[str] | None,
        pip_reqs_by_color: dict[str, list[tuple[int, int]]] | None,
    ) -> dict[str, int]:
        return color_floors(identity, pip_reqs_by_color)


class HypergeometricStrategy(ManaTargetStrategy):
    """Curve/pip-weighted target from hypergeometric.py, wrapped in the same output shapes."""

    def land_target(self, land_count: int, curve_by_cmc: dict[int, int] | None) -> dict:
        target = recommended_land_count(curve_by_cmc or {})
        return band_alert(land_count, target - HYPERGEOMETRIC_LAND_BAND, target + HYPERGEOMETRIC_LAND_BAND)

    def color_floors(
        self,
        identity: list[str] | None,
        pip_reqs_by_color: dict[str, list[tuple[int, int]]] | None,
    ) -> dict[str, int]:
        pip_reqs_by_color = pip_reqs_by_color or {c: [] for c in COLORS}
        shares = color_pip_share(pip_reqs_by_color)
        return {
            color: recommended_color_sources(pip_reqs_by_color.get(color, []), shares.get(color, 0.0))
            for color in (identity or [])
            if color in COLORS
        }


# Later you can add another provider here:
# class SimulationStrategy(ManaTargetStrategy): ...


STRATEGIES_BY_NAME: dict[str, type[ManaTargetStrategy]] = {
    "static": StaticQuotaStrategy,
    "hypergeometric": HypergeometricStrategy,
}


def strategy_from_name(name: str | None) -> ManaTargetStrategy:
    """Looks up a strategy by DeckState.mana_strategy's string value, falling
    back to the same default diagnose()/diagnose_deck() use on their own.
    """
    cls = STRATEGIES_BY_NAME.get(name or "", HypergeometricStrategy)
    return cls()
