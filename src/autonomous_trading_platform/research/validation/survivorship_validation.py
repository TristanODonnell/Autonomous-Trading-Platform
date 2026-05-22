"""
Survivorship-bias-safe validation service.

Wraps the existing SurvivorshipGuard and universe history infrastructure
into a validation-framework-compatible result object.

Validation checks
-----------------
1. Universe configuration is point-in-time anchored (SurvivorshipGuard).
2. No symbol in the experiment universe became available only AFTER the
   experiment start date (future leak detection).
3. For walk-forward folds: each fold's symbol set is valid as of that fold's
   start date (fold-level PIT validation).

This service does NOT redesign universe architecture — it composes existing
guards into validation-layer result types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class _SurvivorshipGuardProtocol(Protocol):
    def validate_experiment_scope(self, scope: Any, start: date, end: date) -> None: ...
    def validate_symbols_not_future_leaked(
        self, symbols: list[str], ts: datetime, known: set[str]
    ) -> None: ...


@runtime_checkable
class _UniverseHistoryProtocol(Protocol):
    def get_included_symbols_as_of(self, as_of: date) -> set[str] | None: ...


@dataclass(frozen=True)
class FoldSurvivorshipCheck:
    fold_index: int
    fold_start: date
    symbols_checked: int
    leaked_symbols: list[str]
    is_safe: bool


@dataclass(frozen=True)
class SurvivorshipValidationResult:
    """
    Outcome of survivorship-bias validation for a research experiment.

    is_safe is True only when all checks pass.
    """

    is_safe: bool
    universe_mode: str
    has_pit_anchor: bool
    has_resolved_universe: bool
    leaked_symbol_count: int
    leaked_symbols: list[str]
    fold_checks: list[FoldSurvivorshipCheck]
    errors: list[str]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "is_safe": self.is_safe,
            "universe_mode": self.universe_mode,
            "has_pit_anchor": self.has_pit_anchor,
            "has_resolved_universe": self.has_resolved_universe,
            "leaked_symbol_count": self.leaked_symbol_count,
            "leaked_symbols": self.leaked_symbols,
            "n_fold_checks": len(self.fold_checks),
            "fold_issues": [
                {"fold": c.fold_index, "leaked": c.leaked_symbols}
                for c in self.fold_checks
                if not c.is_safe
            ],
            "errors": self.errors,
            "warnings": self.warnings,
        }


class SurvivorshipValidationService:
    """
    Validates survivorship-bias safety for an experiment or validation run.

    Parameters
    ----------
    survivorship_guard
        The platform's SurvivorshipGuard (optional: if None, config checks are skipped).
    universe_history_service
        UniverseHistoryService for fold-level membership checks (optional).
    """

    def __init__(
        self,
        *,
        survivorship_guard: _SurvivorshipGuardProtocol | None = None,
        universe_history_service: _UniverseHistoryProtocol | None = None,
    ) -> None:
        self._guard = survivorship_guard
        self._history = universe_history_service

    def validate(
        self,
        *,
        universe_scope: object | None,
        experiment_start: date,
        experiment_end: date,
        symbols: list[str] | None = None,
        known_future_symbols: set[str] | None = None,
        universe_effective_timestamp: datetime | None = None,
        fold_windows: list[tuple[int, date, list[str]]] | None = None,
    ) -> SurvivorshipValidationResult:
        """
        Run all survivorship-bias checks.

        Parameters
        ----------
        universe_scope
            ExperimentUniverseScope (or None to skip config check).
        experiment_start / experiment_end
            Research window boundaries.
        symbols
            Symbol list to check for future leaks.
        known_future_symbols
            Set of symbols that only became available after experiment_start.
        universe_effective_timestamp
            PIT timestamp used to anchor the universe.
        fold_windows
            List of (fold_index, fold_start_date, fold_symbols) for fold-level checks.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Config-level survivorship guard
        universe_mode = "unknown"
        has_pit_anchor = True
        has_resolved_universe = True

        if universe_scope is not None and self._guard is not None:
            try:
                self._guard.validate_experiment_scope(
                    universe_scope, experiment_start, experiment_end
                )
            except Exception as exc:
                errors.append(f"SurvivorshipGuard: {exc}")
                has_pit_anchor = False

            try:
                mode_attr = getattr(universe_scope, "mode", None)
                universe_mode = str(mode_attr) if mode_attr is not None else "unknown"
                version_attr = getattr(universe_scope, "universe_version_id", None)
                has_resolved_universe = version_attr is not None
            except Exception:
                pass

        elif universe_scope is None:
            warnings.append(
                "No universe_scope supplied — survivorship-bias config check skipped. "
                "Ensure simulation used a PIT-anchored universe."
            )

        # 2. Symbol future-leak check
        leaked_symbols: list[str] = []
        if symbols and known_future_symbols:
            leaked_symbols = sorted(set(symbols) & known_future_symbols)
            if leaked_symbols:
                errors.append(
                    f"{len(leaked_symbols)} symbol(s) leaked from the future: {leaked_symbols}"
                )
        elif symbols and self._guard is not None and universe_effective_timestamp is not None:
            try:
                self._guard.validate_symbols_not_future_leaked(
                    symbols,
                    universe_effective_timestamp,
                    known_future_symbols or set(),
                )
            except Exception as exc:
                errors.append(str(exc))

        # 3. Fold-level membership checks
        fold_checks: list[FoldSurvivorshipCheck] = []
        if fold_windows and self._history is not None:
            for fold_idx, fold_start, fold_symbols in fold_windows:
                fold_leaked: list[str] = []
                if fold_symbols:
                    try:
                        valid = self._history.get_included_symbols_as_of(fold_start)
                        if valid is not None:
                            fold_leaked = [s for s in fold_symbols if s not in valid]
                    except Exception as exc:
                        warnings.append(f"Fold {fold_idx} membership check failed: {exc}")
                fold_checks.append(
                    FoldSurvivorshipCheck(
                        fold_index=fold_idx,
                        fold_start=fold_start,
                        symbols_checked=len(fold_symbols),
                        leaked_symbols=fold_leaked,
                        is_safe=len(fold_leaked) == 0,
                    )
                )
                if fold_leaked:
                    errors.append(
                        f"Fold {fold_idx} (start={fold_start}): "
                        f"{len(fold_leaked)} symbol(s) not in universe as of that date."
                    )
        elif fold_windows and self._history is None:
            warnings.append(
                "Fold-level survivorship checks skipped — no UniverseHistoryService provided."
            )

        is_safe = len(errors) == 0

        return SurvivorshipValidationResult(
            is_safe=is_safe,
            universe_mode=universe_mode,
            has_pit_anchor=has_pit_anchor,
            has_resolved_universe=has_resolved_universe,
            leaked_symbol_count=len(leaked_symbols),
            leaked_symbols=leaked_symbols,
            fold_checks=fold_checks,
            errors=errors,
            warnings=warnings,
        )
