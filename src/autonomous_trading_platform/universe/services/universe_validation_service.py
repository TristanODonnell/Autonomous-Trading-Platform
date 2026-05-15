from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

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
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember as UniverseMemberRow,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseVersion as UniverseVersionRow,
)
from autonomous_trading_platform.universe.types import UniverseValidationResult


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

            missing_symbols = self._find_symbols_missing_from_dataset(symbols)
            if missing_symbols:
                errors.append(
                    "UniverseVersion contains symbols missing from dataset: "
                    + ", ".join(sorted(missing_symbols)[:10])
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
        return self.validate(version_contract, member_contracts)

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
