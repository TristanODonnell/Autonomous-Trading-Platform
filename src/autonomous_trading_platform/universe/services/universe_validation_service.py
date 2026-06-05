from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import UniverseStatus
from autonomous_trading_platform.contracts.runtime.universe_version import (
    UniverseMember as UniverseMemberContract,
)
from autonomous_trading_platform.contracts.runtime.universe_version import (
    UniverseVersion as UniverseVersionContract,
)
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.universe_version import (
    UNIVERSE_MEMBER_RULES,
    UNIVERSE_VERSION_RULES,
)
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import record_universe_validation_metrics
from autonomous_trading_platform.observability.runtime_context import get_runtime_context
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.raw_market_pool import RawMarketSymbol
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember as UniverseMemberRow,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseVersion as UniverseVersionRow,
)
from autonomous_trading_platform.universe.types import UniverseValidationResult

logger = get_logger(__name__)


class UniverseValidationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def validate(
        self,
        version: UniverseVersionContract,
        members: list[UniverseMemberContract],
    ) -> UniverseValidationResult:
        errors: list[str] = []

        version_result = run_rules(version, UNIVERSE_VERSION_RULES)
        errors.extend(self._format_rule_errors(version_result))

        if not members:
            errors.append("UniverseVersion must have at least one member symbol")
        else:
            for member in members:
                member_result = run_rules(member, UNIVERSE_MEMBER_RULES)
                errors.extend(self._format_rule_errors(member_result))

            symbols = [m.symbol for m in members]
            if len(symbols) != len(set(symbols)):
                errors.append("UniverseVersion members must not contain duplicate symbols")

            included = [m for m in members if m.excluded_reason is None]
            included_ranks = [m.rank for m in included]
            if version.source != "custom" and any(
                rank is None or rank < 0 for rank in included_ranks
            ):
                errors.append("UniverseVersion included members must have non-negative ranks")
            if len([rank for rank in included_ranks if rank is not None]) != len(
                set(rank for rank in included_ranks if rank is not None)
            ):
                errors.append("UniverseVersion included member ranks must be unique")
            missing_liquidity = [
                m.symbol
                for m in included
                if m.liquidity_metrics_json is None and version.source != "custom"
            ]
            if missing_liquidity:
                errors.append(
                    "UniverseVersion included members missing liquidity data: "
                    + ", ".join(sorted(missing_liquidity)[:10])
                )

            missing_symbols = self._find_symbols_missing_from_dataset(symbols)
            if missing_symbols:
                errors.append(
                    "UniverseVersion contains symbols missing from dataset: "
                    + ", ".join(sorted(missing_symbols)[:10])
                )

        self._emit_validation_result(
            errors=errors,
            universe_version_id=version.universe_version_id,
            member_count=len(members),
        )
        return UniverseValidationResult(ok=not errors, errors=errors)

    def validate_version_row(
        self,
        version: UniverseVersionRow,
        members: list[UniverseMemberRow],
    ) -> UniverseValidationResult:
        version_contract = UniverseVersionContract(
            universe_version_id=version.universe_version_id,
            name=version.name,
            source=version.source,
            created_at=version.created_at,
            effective_from=version.effective_from,
            effective_to=version.effective_to,
            status=version.status,
            rebalance_reason=version.rebalance_reason,
            config_hash=version.config_hash,
            generation_metadata_json=version.generation_metadata_json,
        )
        member_contracts = [
            UniverseMemberContract(
                universe_version_id=m.universe_version_id,
                symbol=m.symbol,
                rank=m.rank,
                score=m.score,
                included_reason=m.included_reason,
                excluded_reason=m.excluded_reason,
                liquidity_metrics_json=m.liquidity_metrics_json,
                quality_metrics_json=m.quality_metrics_json,
            )
            for m in members
        ]
        result = self.validate(version_contract, member_contracts)
        errors = list(result.errors)
        included_members = [m for m in members if m.excluded_reason is None]

        expected_count = None
        if version.generation_metadata_json:
            expected_count = version.generation_metadata_json.get("included_count")
            if expected_count is None:
                expected_count = version.generation_metadata_json.get("target_universe_size")
        if expected_count is not None and int(expected_count) != len(included_members):
            errors.append(
                f"UniverseVersion member count mismatch: expected {expected_count}, "
                f"found {len(included_members)}"
            )

        if version.status == UniverseStatus.ACTIVE and version.effective_to is not None:
            errors.append("UniverseVersion active status is invalid with non-null effective_to")

        inactive_symbols = self._find_inactive_or_delisted_symbols(
            [m.symbol for m in included_members]
        )
        if inactive_symbols:
            errors.append(
                "UniverseVersion contains inactive or delisted symbols: "
                + ", ".join(sorted(inactive_symbols)[:10])
            )

        self._emit_validation_result(
            errors=errors,
            universe_version_id=version.universe_version_id,
            member_count=len(members),
        )
        return UniverseValidationResult(ok=not errors, errors=errors)

    def validate_active(self, as_of: datetime) -> UniverseValidationResult:
        stmt = select(UniverseVersionRow).where(
            UniverseVersionRow.status == UniverseStatus.ACTIVE,
            UniverseVersionRow.effective_from <= as_of,
            (UniverseVersionRow.effective_to.is_(None)) | (UniverseVersionRow.effective_to > as_of),
        )
        active_versions = list(self.session.execute(stmt).scalars().all())
        errors: list[str] = []

        if not active_versions:
            errors.append("No active universe found")
        if len(active_versions) > 1:
            errors.append("Multiple active universes found")

        for version in active_versions:
            if version.status != UniverseStatus.ACTIVE:
                errors.append(f"Invalid active status: {version.status}")
            overlap_stmt = select(UniverseVersionRow).where(
                UniverseVersionRow.universe_version_id != version.universe_version_id,
                UniverseVersionRow.status.in_([UniverseStatus.ACTIVE, UniverseStatus.RETIRED]),
                UniverseVersionRow.effective_from < (version.effective_to or as_of),
                (UniverseVersionRow.effective_to.is_(None))
                | (UniverseVersionRow.effective_to > version.effective_from),
            )
            overlapping = list(self.session.execute(overlap_stmt).scalars().all())
            if overlapping:
                errors.append("Invalid effective timestamp overlap")
            members = self._get_members(version.universe_version_id)
            row_result = self.validate_version_row(version, members)
            errors.extend(row_result.errors)

        unique_errors = list(dict.fromkeys(errors))
        self._emit_validation_result(
            errors=unique_errors,
            universe_version_id=active_versions[0].universe_version_id if active_versions else None,
            member_count=sum(
                len(self._get_members(v.universe_version_id)) for v in active_versions
            ),
        )
        return UniverseValidationResult(ok=not unique_errors, errors=unique_errors)

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

    def _find_symbols_missing_from_dataset(self, symbols: list[str]) -> list[str]:
        if not symbols:
            return []
        try:
            rows = (
                self.session.execute(
                    select(MarketBar.symbol).where(MarketBar.symbol.in_(symbols)).distinct()
                )
                .scalars()
                .all()
            )
        except Exception:
            try:
                rows = (
                    self.session.execute(
                        text(
                            "SELECT DISTINCT symbol FROM market_bars WHERE symbol = ANY(:symbols)"
                        ),
                        {"symbols": symbols},
                    )
                    .scalars()
                    .all()
                )
            except Exception:
                return []
        found = set(rows)
        return [symbol for symbol in symbols if symbol not in found]

    def _find_inactive_or_delisted_symbols(self, symbols: list[str]) -> list[str]:
        if not symbols:
            return []
        if not hasattr(self.session, "get_bind"):
            return []
        try:
            rows = (
                self.session.execute(
                    select(RawMarketSymbol.symbol).where(
                        RawMarketSymbol.symbol.in_(symbols),
                        (RawMarketSymbol.is_tradable.is_(False))
                        | (RawMarketSymbol.status.in_(["inactive", "delisted"])),
                    )
                )
                .scalars()
                .all()
            )
        except Exception:
            return []
        return list(rows)

    def _get_members(self, version_id: str) -> list[UniverseMemberRow]:
        stmt = select(UniverseMemberRow).where(UniverseMemberRow.universe_version_id == version_id)
        return list(self.session.execute(stmt).scalars().all())

    def _emit_validation_result(
        self,
        *,
        errors: list[str],
        universe_version_id: str | None,
        member_count: int,
    ) -> None:
        invalid_count = len(errors)
        record_universe_validation_metrics(
            invalid_member_count=invalid_count,
            failure_count=invalid_count,
        )
        if not errors:
            return
        ctx = get_runtime_context()
        logger.error(
            "universe validation failure",
            extra={
                **LogContext(
                    component="universe_validation",
                    step="universe_validation_failure",
                    universe_version_id=universe_version_id,
                    correlation_id=ctx.correlation_id if ctx else None,
                    job_run_id=ctx.job_run_id if ctx else None,
                ).to_extra(),
                "member_count": member_count,
                "errors": errors,
            },
        )
