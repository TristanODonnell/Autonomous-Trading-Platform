from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval
from autonomous_trading_platform.contracts.runtime.universe_snapshot import (
    UniverseSnapshot as UniverseSnapshotContract,
)
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.universe_snapshot import (
    UNIVERSE_SNAPSHOT_RULES,
)
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.universe_snapshots import (
    UniverseSnapshot as UniverseSnapshotRow,
)
from autonomous_trading_platform.universe.types import UniverseValidationResult


class UniverseValidationService:
    """
    Validates UniverseSnapshot contracts and optionally adapts from
    database row models.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def validate(
        self,
        snapshot: UniverseSnapshotContract,
    ) -> UniverseValidationResult:
        errors: list[str] = []

        if not snapshot.symbols:
            errors.append("UniverseSnapshot must contain at least one symbol")

        rule_result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)
        errors.extend(self._format_rule_errors(rule_result))

        missing_symbols = self._find_symbols_missing_from_dataset(snapshot.symbols)
        if missing_symbols:
            errors.append(
                "UniverseSnapshot contains symbols missing from dataset: "
                + ", ".join(sorted(missing_symbols)[:10])
            )

        lookback_days_raw = snapshot.criteria.get("lookback_days")
        selection_timestamp_raw = snapshot.criteria.get("selection_timestamp")

        if lookback_days_raw is not None and selection_timestamp_raw is not None:
            try:
                lookback_days, as_of = self._parse_coverage_criteria(
                    lookback_days_raw=lookback_days_raw,
                    selection_timestamp_raw=selection_timestamp_raw,
                )
            except (TypeError, ValueError):
                errors.append(
                    "UniverseSnapshot criteria has invalid lookback_days or selection_timestamp"
                )
            else:
                if lookback_days <= 0:
                    errors.append("lookback_days must be positive")
                else:
                    min_days_required = max(1, int(lookback_days * 0.8))

                    insufficient_symbols = self._find_symbols_with_insufficient_recent_coverage(
                        symbols=snapshot.symbols,
                        as_of=as_of,
                        lookback_days=lookback_days,
                        min_days_required=min_days_required,
                    )
                    if insufficient_symbols:
                        errors.append(
                            "UniverseSnapshot contains symbols with insufficient recent coverage: "
                            + ", ".join(sorted(insufficient_symbols)[:10])
                        )

        return UniverseValidationResult(
            ok=not errors,
            errors=errors,
        )

    def validate_row(
        self,
        snapshot: UniverseSnapshotRow,
    ) -> UniverseValidationResult:
        """
        Adapter that converts a DB row to a contract before validation.
        """
        contract = self._to_contract(snapshot)
        return self.validate(contract)

    def _to_contract(
        self,
        snapshot: UniverseSnapshotRow,
    ) -> UniverseSnapshotContract:
        return UniverseSnapshotContract(
            universe_id=snapshot.universe_id,
            snapshot_date=snapshot.snapshot_date,
            effective_start=snapshot.effective_start,
            effective_end=snapshot.effective_end,
            symbols=snapshot.symbols,
            criteria=snapshot.criteria,
            version=snapshot.version,
            source=snapshot.source,
            built_at=snapshot.built_at,
            notes=snapshot.notes,
        )

    def _format_rule_errors(self, rule_result: object) -> list[str]:
        formatted: list[str] = []

        violations = getattr(rule_result, "violations", [])
        for violation in violations:
            code = getattr(violation, "code", "UNKNOWN")
            message = getattr(violation, "message", str(violation))
            field = getattr(violation, "field", None)

            if field:
                formatted.append(f"{code} [{field}]: {message}")
            else:
                formatted.append(f"{code}: {message}")

        return formatted

    @staticmethod
    def _parse_coverage_criteria(
        *,
        lookback_days_raw: Any,
        selection_timestamp_raw: Any,
    ) -> tuple[int, datetime]:
        lookback_days = int(lookback_days_raw)

        if not isinstance(selection_timestamp_raw, str):
            raise TypeError("selection_timestamp must be an ISO-8601 string")

        as_of = datetime.fromisoformat(selection_timestamp_raw)
        return lookback_days, as_of

    def _find_symbols_missing_from_dataset(
        self,
        symbols: list[str],
    ) -> list[str]:
        """
        Checks whether universe symbols exist in the market dataset.
        """
        if not symbols:
            return []

        rows = (
            self.session.execute(
                text(
                    """
                    SELECT DISTINCT symbol
                    FROM market_bars
                    WHERE symbol = ANY(:symbols)
                    """
                ),
                {"symbols": symbols},
            )
            .scalars()
            .all()
        )

        found = set(rows)
        return [symbol for symbol in symbols if symbol not in found]

    def _find_symbols_with_insufficient_recent_coverage(
        self,
        *,
        symbols: list[str],
        as_of: datetime,
        lookback_days: int,
        min_days_required: int,
    ) -> list[str]:
        if not symbols:
            return []

        window_start = as_of - timedelta(days=lookback_days)
        stmt = (
            select(
                MarketBar.symbol,
                func.count(func.distinct(func.date(MarketBar.timestamp))).label("day_count"),
            )
            .where(
                MarketBar.symbol.in_(symbols),
                MarketBar.interval == BarInterval.FIVE_MIN,
                MarketBar.timestamp >= window_start,
                MarketBar.timestamp <= as_of,
            )
            .group_by(MarketBar.symbol)
        )

        rows = self.session.execute(stmt).all()
        counts = {symbol: day_count for symbol, day_count in rows}

        insufficient: list[str] = []
        for symbol in symbols:
            if counts.get(symbol, 0) < min_days_required:
                insufficient.append(symbol)

        return insufficient
