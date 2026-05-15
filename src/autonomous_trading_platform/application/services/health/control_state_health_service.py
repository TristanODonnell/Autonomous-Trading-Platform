from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.detailed_health import (
    HealthCheckName,
    HealthCheckResult,
    HealthSeverity,
    HealthStatus,
    ServiceHealthReport,
)
from autonomous_trading_platform.execution.services.trading_freeze_service import (
    TradingFreezeService,
)
from autonomous_trading_platform.storage.sor.repositories.queries.runtime_soak_verification_repository import (
    RuntimeSoakVerificationRepository,
)


def _ok(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.OK,
        severity=HealthSeverity.INFO,
        message=message,
        metadata=metadata,
    )


def _degraded(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.DEGRADED,
        severity=HealthSeverity.WARNING,
        message=message,
        metadata=metadata,
    )


def _critical(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.CRITICAL,
        severity=HealthSeverity.CRITICAL,
        message=message,
        metadata=metadata,
    )


def _derive_status(checks: list[HealthCheckResult]) -> HealthStatus:
    if any(c.status == HealthStatus.CRITICAL for c in checks):
        return HealthStatus.CRITICAL
    if any(c.status == HealthStatus.DEGRADED for c in checks):
        return HealthStatus.DEGRADED
    return HealthStatus.OK


class ControlStateHealthService:
    """
    TASK-614 — reports the current kill-switch, pause, freeze, and
    degradation state.

    All checks are INFORMATIONAL about current state — CRITICAL/DEGRADED
    here means trading is blocked, not that something is broken.
    """

    def __init__(
        self,
        session: Session,
        *,
        repository: RuntimeSoakVerificationRepository | None = None,
        freeze_service: TradingFreezeService | None = None,
    ) -> None:
        self._session = session
        self._repo = repository or RuntimeSoakVerificationRepository(session)
        self._freeze_service = freeze_service or TradingFreezeService()

    def check(self) -> ServiceHealthReport:
        control_state = self._repo.get_current_runtime_control_state()
        risk_snapshot = self._repo.get_latest_risk_snapshot()
        checks = [
            self._check_kill_switch(control_state),
            self._check_pause_state(control_state),
            self._check_freeze_state(),
            self._check_degradation_state(control_state, risk_snapshot),
        ]
        return ServiceHealthReport(
            service="control_state",
            status=_derive_status(checks),
            checks=checks,
        )

    def _check_kill_switch(self, control_state) -> HealthCheckResult:
        if control_state is None:
            return _degraded(
                HealthCheckName.CONTROL_KILL_SWITCH,
                "No runtime control state record found — cannot determine kill switch status.",
                {"control_state_found": False},
            )

        metadata = {
            "kill_switch_enabled": control_state.kill_switch_enabled,
            "reason": control_state.reason,
            "updated_by": control_state.updated_by,
            "updated_at": control_state.updated_at.isoformat(),
        }

        if control_state.kill_switch_enabled:
            return _critical(
                HealthCheckName.CONTROL_KILL_SWITCH,
                f"Kill switch is ENABLED — all trading blocked. Reason: {control_state.reason}",
                metadata,
            )
        return _ok(
            HealthCheckName.CONTROL_KILL_SWITCH,
            "Kill switch is off.",
            metadata,
        )

    def _check_pause_state(self, control_state) -> HealthCheckResult:
        if control_state is None:
            return _degraded(
                HealthCheckName.CONTROL_PAUSE_STATE,
                "No runtime control state record found — cannot determine pause status.",
                {"control_state_found": False},
            )

        metadata = {
            "trading_paused": control_state.trading_paused,
            "trading_enabled": control_state.trading_enabled,
            "trading_mode": control_state.trading_mode,
            "reason": control_state.reason,
            "updated_at": control_state.updated_at.isoformat(),
        }

        if not control_state.trading_enabled:
            return _critical(
                HealthCheckName.CONTROL_PAUSE_STATE,
                "Trading is DISABLED. Reason: " + (control_state.reason or "none"),
                metadata,
            )
        if control_state.trading_paused:
            return _degraded(
                HealthCheckName.CONTROL_PAUSE_STATE,
                "Trading is PAUSED. Reason: " + (control_state.reason or "none"),
                metadata,
            )
        return _ok(
            HealthCheckName.CONTROL_PAUSE_STATE,
            f"Trading is active in {control_state.trading_mode} mode.",
            metadata,
        )

    def _check_freeze_state(self) -> HealthCheckResult:
        is_frozen = self._freeze_service.is_trading_frozen()
        metadata = {"trading_frozen": is_frozen}

        if is_frozen:
            return _critical(
                HealthCheckName.CONTROL_FREEZE_STATE,
                "Trading is FROZEN — mid-cycle freeze triggered by a critical failure.",
                metadata,
            )
        return _ok(
            HealthCheckName.CONTROL_FREEZE_STATE,
            "No trading freeze active.",
            metadata,
        )

    def _check_degradation_state(self, control_state, risk_snapshot) -> HealthCheckResult:
        risk_blocked = risk_snapshot is not None and getattr(risk_snapshot, "is_blocked", False)
        block_reasons = getattr(risk_snapshot, "block_reasons", None) if risk_blocked else None

        trading_mode = getattr(control_state, "trading_mode", None) if control_state else None
        metadata = {
            "risk_blocked": risk_blocked,
            "block_reasons": block_reasons,
            "trading_mode": trading_mode,
            "risk_snapshot_id": (
                str(risk_snapshot.snapshot_id) if risk_snapshot is not None else None
            ),
            "risk_snapshot_timestamp": (
                risk_snapshot.timestamp.isoformat() if risk_snapshot is not None else None
            ),
        }

        if risk_blocked:
            return _critical(
                HealthCheckName.CONTROL_DEGRADATION_STATE,
                f"Risk engine has blocked trading. Reasons: {block_reasons}",
                metadata,
            )

        if risk_snapshot is None:
            return _degraded(
                HealthCheckName.CONTROL_DEGRADATION_STATE,
                "No risk snapshot found — cannot confirm risk state.",
                metadata,
            )

        return _ok(
            HealthCheckName.CONTROL_DEGRADATION_STATE,
            "No risk-based degradation detected.",
            metadata,
        )
