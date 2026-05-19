from __future__ import annotations

from datetime import date, datetime

from autonomous_trading_platform.universe.types import (
    ExperimentUniverseScope,
    UniverseResolutionMode,
)


class SurvivorshipBiasError(RuntimeError):
    """
    Raised when a historical research configuration is unsafe due to survivorship bias.

    Survivorship bias occurs when a backtest accidentally uses knowledge that only
    existed after the simulation period — e.g., choosing symbols that survived to
    today rather than symbols that existed at the historical start date.
    """


class SurvivorshipGuard:
    """
    Validates historical research configurations for survivorship-bias safety.

    Called before any simulation or backtest begins. Raises SurvivorshipBiasError
    for configurations that could inadvertently use forward-looking information.

    Design contract:
    - All active modes (non-CUSTOM) must be PIT-anchored (effective_timestamp set).
    - ACTIVE_AS_OF_START_DATE with no resolved universe → fail: no symbol set available.
    - HISTORICAL_ROTATION_REPLAY with no transitions → fail: replay window is empty.
    - CUSTOM mode is always safe: the user supplied the list explicitly.
    - FIXED_UNIVERSE_VERSION is always safe: the resolver already validated the version exists.
    """

    def validate_experiment_scope(
        self,
        scope: ExperimentUniverseScope,
        experiment_start: date,
        experiment_end: date,
    ) -> None:
        """
        Raise SurvivorshipBiasError for any unsafe universe configuration.

        Must be called before simulation begins.
        """
        self._check_active_as_of_has_universe(scope, experiment_start)
        self._check_replay_has_transitions(scope, experiment_start, experiment_end)
        self._check_has_pit_anchor(scope, experiment_start)

    def validate_symbols_not_future_leaked(
        self,
        symbols: list[str],
        universe_effective_timestamp: datetime,
        known_future_symbols: set[str],
    ) -> None:
        """
        Raise if any symbols only appeared after ``universe_effective_timestamp``.

        ``known_future_symbols`` should be built from corporate-action or IPO records
        that postdate the effective timestamp.
        """
        leaked = set(symbols) & known_future_symbols
        if leaked:
            raise SurvivorshipBiasError(
                f"Survivorship bias detected: {len(leaked)} symbol(s) in the experiment "
                f"universe did not exist as of {universe_effective_timestamp.isoformat()}. "
                f"Leaked symbols: {sorted(leaked)}. "
                "These would introduce forward-looking bias into historical research."
            )

    def _check_active_as_of_has_universe(
        self, scope: ExperimentUniverseScope, experiment_start: date
    ) -> None:
        if (
            scope.mode == UniverseResolutionMode.ACTIVE_AS_OF_START_DATE
            and scope.universe_version_id is None
        ):
            raise SurvivorshipBiasError(
                f"Experiment starting {experiment_start} requested ACTIVE_AS_OF_START_DATE "
                "resolution but no active universe version was found for that date. "
                "Simulating with an undefined symbol set produces meaningless results. "
                "Seed a historical universe first or switch to CUSTOM mode with an explicit "
                "symbol list."
            )

    def _check_replay_has_transitions(
        self,
        scope: ExperimentUniverseScope,
        experiment_start: date,
        experiment_end: date,
    ) -> None:
        if scope.replay_rotation_enabled and not scope.replay_transitions:
            raise SurvivorshipBiasError(
                f"HISTORICAL_ROTATION_REPLAY requested for [{experiment_start}, {experiment_end}] "
                "but the resolved scope has no transitions. The replay window appears to have "
                "no recorded universe rotations. "
                "Use 'universe replay-timeline' to inspect available history."
            )

    def _check_has_pit_anchor(self, scope: ExperimentUniverseScope, experiment_start: date) -> None:
        if (
            scope.mode != UniverseResolutionMode.CUSTOM
            and scope.effective_timestamp is None
            and scope.universe_version_id is not None
        ):
            raise SurvivorshipBiasError(
                f"Experiment starting {experiment_start} has a resolved universe "
                f"(version_id={scope.universe_version_id}) but no effective_timestamp. "
                "All historical simulations must use a point-in-time timestamp anchor. "
                "This indicates a resolver bug — report to the platform team."
            )
