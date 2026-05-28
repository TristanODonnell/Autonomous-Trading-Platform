# autonomous_trading_platform/execution/services/portfolio_drawdown_governance_service.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PortfolioDrawdownAction(StrEnum):
    """
    Governance response triggered when portfolio drawdown exceeds the threshold.

    WARN_ONLY emits audit/metrics but does not block any trading or rebalancing.
    This is suitable for initial deployment where operators want visibility before
    activating hard blocks.

    PAUSE_NEW_TRADING blocks the trading cycle (new order generation) but allows
    in-flight positions to settle naturally.  Rebalancing continues so existing
    allocations can be managed.

    PAUSE_REBALANCING blocks the rebalance cycle, freezing allocations at their
    current weights.  Trading continues so existing strategies can act on signals.

    ACTIVATE_KILL_SWITCH is the most severe response: activates the global kill
    switch, pauses trading and rebalancing, and requires the operator to explicitly
    disable the kill switch AND acknowledge the governance breach before any
    trading resumes.
    """

    WARN_ONLY = "warn_only"
    PAUSE_NEW_TRADING = "pause_new_trading"
    PAUSE_REBALANCING = "pause_rebalancing"
    ACTIVATE_KILL_SWITCH = "activate_kill_switch"


class PortfolioRecoveryMode(StrEnum):
    """
    How the system should behave after a breach is resolved (drawdown recovers).

    MANUAL_RESUME_REQUIRED (default, safest): the governance pause persists until
    an operator explicitly calls acknowledge_breach().  The system will not
    automatically resume even if the drawdown falls below the threshold.

    RECOVERY_OBSERVATION: the system records that drawdown has recovered and logs
    the recovery, but still requires an explicit operator acknowledgement.  The
    difference from MANUAL_RESUME_REQUIRED is that operators can query the recovery
    state to see the drawdown recovered at time T without the system resuming
    automatically.

    AUTO_RESUME: the governance pause is automatically lifted when the portfolio
    drawdown falls back below the configured threshold on any subsequent evaluate()
    call.  Use only for low-severity thresholds or in testing environments —
    automatic resumption carries real operational risk in live trading.
    """

    MANUAL_RESUME_REQUIRED = "manual_resume_required"
    RECOVERY_OBSERVATION = "recovery_observation"
    AUTO_RESUME = "auto_resume"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioDrawdownConfig:
    """
    Runtime configuration for portfolio drawdown governance.

    Constructed from OperatorSettings at cycle entry so it always reflects
    the current operator-configured values without requiring service restarts.

    enabled: master switch.  When False, evaluate() is a no-op and all block
        checks return False.  Useful for gradual rollout or emergency override.

    max_drawdown_pct: drawdown threshold in [0, 1].  A value of 0.15 means the
        circuit breaker fires when the portfolio is more than 15% below its
        all-time peak equity.  Defaults to 0.15 (conservative but not hair-trigger).

    action: governance response to take when the threshold is breached.

    recovery_mode: how the system behaves once drawdown recovers below the
        threshold.

    auto_kill_switch: when True, the global kill switch is also activated on
        breach regardless of `action`.  Only relevant when `action` is not
        already ACTIVATE_KILL_SWITCH.  Requires a KillSwitchService to be
        injected at construction time; if not injected the flag is ignored and
        a warning is logged.
    """

    enabled: bool = True
    max_drawdown_pct: float = 0.15
    action: PortfolioDrawdownAction = PortfolioDrawdownAction.PAUSE_NEW_TRADING
    recovery_mode: PortfolioRecoveryMode = PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED
    auto_kill_switch: bool = False

    @classmethod
    def from_settings(cls, settings_row) -> PortfolioDrawdownConfig:
        """
        Build config from an OperatorSettingsRow (or any object with the same
        attributes via getattr).  Applies safe defaults for every field so the
        service degrades gracefully if a column has not yet been migrated.
        """
        max_dd = float(getattr(settings_row, "portfolio_max_drawdown_pct", None) or 0.15)

        action_str = str(
            getattr(settings_row, "portfolio_drawdown_action", None) or "pause_new_trading"
        )
        recovery_str = str(
            getattr(settings_row, "portfolio_drawdown_recovery_mode", None)
            or "manual_resume_required"
        )

        try:
            action = PortfolioDrawdownAction(action_str)
        except ValueError:
            logger.warning(
                "portfolio_governance.unknown_action_falling_back",
                extra={"action": action_str},
            )
            action = PortfolioDrawdownAction.PAUSE_NEW_TRADING

        try:
            recovery = PortfolioRecoveryMode(recovery_str)
        except ValueError:
            logger.warning(
                "portfolio_governance.unknown_recovery_mode_falling_back",
                extra={"recovery_mode": recovery_str},
            )
            recovery = PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED

        auto_ks = bool(getattr(settings_row, "portfolio_auto_kill_switch", False) or False)

        return cls(
            enabled=True,
            max_drawdown_pct=max_dd,
            action=action,
            recovery_mode=recovery,
            auto_kill_switch=auto_ks,
        )


# ---------------------------------------------------------------------------
# Result / evaluation output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioGovernanceEvaluation:
    """
    Immutable output of a single evaluate() call.

    All numeric fields are in the same units as the inputs (USD for equity,
    fraction [0,1] for drawdown/threshold).  Callers can emit these directly
    into structured logs, metrics, or API responses without conversion.
    """

    current_equity: float
    peak_equity: float
    drawdown_pct: float
    threshold_pct: float
    drawdown_utilization: float  # drawdown_pct / threshold_pct; > 1.0 means breached
    breach_active: bool  # True if a breach is on record and not yet acknowledged
    newly_breached: bool  # True only on the first evaluate() that detects a breach
    trading_blocked: bool
    rebalancing_blocked: bool
    triggered_action: str | None
    recovery_mode: str | None
    kill_switch_activated: bool  # True if kill switch was activated during this call


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PortfolioDrawdownGovernanceService:
    """
    Evaluates portfolio-level drawdown and enforces governance actions.

    Design principles
    -----------------
    * Fail-closed on breach: when a breach is detected the configured action
      is applied immediately and persisted before the method returns.
    * Fail-open on infrastructure errors: if peak equity cannot be resolved
      (e.g. first run, no cash snapshot), the service does not block trading.
    * Idempotent: calling evaluate() multiple times with the same equity value
      emits exactly one PORTFOLIO_DRAWDOWN_BREACH event and does not oscillate
      the governance state.
    * Peak equity is monotonically increasing: it is only ever updated upward,
      so a restart after a loss does not accidentally reset the peak and create
      a falsely clean drawdown reading.

    Cycle integration
    -----------------
    evaluate() should be called at the start of both the trading cycle and the
    rebalance cycle, AFTER the kill-switch / runtime-control checks but BEFORE
    any trading or rebalancing work begins.

    is_trading_blocked() and is_rebalancing_blocked() read the persisted state
    without triggering evaluation — they are safe to call in read-only paths.
    """

    def __init__(
        self,
        governance_repo,
        audit_repo,
        kill_switch_service=None,
    ) -> None:
        self._repo = governance_repo
        self._audit = audit_repo
        self._kill_switch = kill_switch_service

    # ------------------------------------------------------------------
    # Primary evaluation entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        current_equity: float,
        config: PortfolioDrawdownConfig,
        now: datetime | None = None,
    ) -> PortfolioGovernanceEvaluation:
        """
        Evaluate portfolio drawdown against the configured threshold and enforce
        governance actions if a breach is detected.

        Side effects on breach (first detection only):
          - Persists updated governance state (governance_paused, rebalancing_paused)
          - Emits PORTFOLIO_DRAWDOWN_BREACH audit event
          - Optionally activates the kill switch
          - Increments OTel breach counter

        Side effects on recovery (when auto_resume mode is active):
          - Clears governance pause state
          - Emits PORTFOLIO_DRAWDOWN_RECOVERY audit event

        Returns PortfolioGovernanceEvaluation reflecting the state AFTER all
        side effects have been applied.
        """
        if now is None:
            now = datetime.now(UTC)

        if not config.enabled:
            logger.debug("portfolio_governance.disabled_skipping_evaluation")
            return PortfolioGovernanceEvaluation(
                current_equity=current_equity,
                peak_equity=current_equity,
                drawdown_pct=0.0,
                threshold_pct=config.max_drawdown_pct,
                drawdown_utilization=0.0,
                breach_active=False,
                newly_breached=False,
                trading_blocked=False,
                rebalancing_blocked=False,
                triggered_action=None,
                recovery_mode=None,
                kill_switch_activated=False,
            )

        state = self._repo.get_current_state()

        # ---- Update peak equity (monotonically increasing) ----
        prior_peak = state.peak_equity_usd
        current_peak = prior_peak if prior_peak is not None else current_equity
        if current_equity > current_peak:
            current_peak = current_equity

        # ---- Compute drawdown ----
        if current_peak <= 0.0:
            drawdown_pct = 0.0
        else:
            drawdown_pct = max(0.0, (current_peak - current_equity) / current_peak)

        drawdown_utilization = (
            drawdown_pct / config.max_drawdown_pct if config.max_drawdown_pct > 0 else 0.0
        )

        # An "active breach" exists when breach_detected_at is set but has not
        # been acknowledged by the operator yet.
        breach_was_active = (
            state.breach_detected_at is not None and state.operator_acknowledged_at is None
        )

        newly_breached = False
        kill_switch_activated = False
        updates: dict = {"peak_equity_usd": current_peak}

        if drawdown_pct > config.max_drawdown_pct and not breach_was_active:
            # ---- First detection of a new breach ----
            newly_breached = True
            updates.update(
                {
                    "breach_detected_at": now,
                    "breach_drawdown_pct": drawdown_pct,
                    "breach_threshold_pct": config.max_drawdown_pct,
                    "triggered_action": config.action.value,
                    "recovery_mode": config.recovery_mode.value,
                    "operator_acknowledged_at": None,
                    "operator_acknowledged_by": None,
                }
            )

            if config.action in (
                PortfolioDrawdownAction.PAUSE_NEW_TRADING,
                PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH,
            ):
                updates["governance_paused"] = True

            if config.action in (
                PortfolioDrawdownAction.PAUSE_REBALANCING,
                PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH,
            ):
                updates["rebalancing_paused"] = True

            # Kill switch activation — either because the action demands it or
            # because auto_kill_switch is set as a secondary escalation.
            should_activate_ks = (
                config.action == PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH
                or config.auto_kill_switch
            )
            if should_activate_ks:
                if self._kill_switch is not None:
                    self._kill_switch.enable(
                        reason=(
                            f"portfolio_drawdown_breach: {drawdown_pct:.4f}"
                            f" > {config.max_drawdown_pct:.4f}"
                        ),
                        updated_by="portfolio_drawdown_governance",
                    )
                    updates["kill_switch_auto_activated"] = True
                    kill_switch_activated = True
                else:
                    logger.warning(
                        "portfolio_governance.kill_switch_requested_but_not_injected",
                        extra={
                            "action": config.action.value,
                            "auto_kill_switch": config.auto_kill_switch,
                        },
                    )

            self._emit_breach_audit(
                now=now,
                current_equity=current_equity,
                peak_equity=current_peak,
                drawdown_pct=drawdown_pct,
                config=config,
                kill_switch_activated=kill_switch_activated,
                drawdown_utilization=drawdown_utilization,
            )

            self._emit_governance_pause_audit(
                now=now,
                action=config.action,
                drawdown_pct=drawdown_pct,
                threshold_pct=config.max_drawdown_pct,
            )

            logger.warning(
                "PORTFOLIO_DRAWDOWN_BREACH",
                extra={
                    "current_equity": current_equity,
                    "peak_equity": current_peak,
                    "drawdown_pct": round(drawdown_pct, 6),
                    "threshold_pct": config.max_drawdown_pct,
                    "drawdown_utilization": round(drawdown_utilization, 4),
                    "triggered_action": config.action.value,
                    "kill_switch_activated": kill_switch_activated,
                },
            )

        elif breach_was_active and drawdown_pct <= config.max_drawdown_pct:
            # ---- Drawdown has recovered below threshold ----
            if config.recovery_mode == PortfolioRecoveryMode.AUTO_RESUME:
                updates.update(
                    {
                        "governance_paused": False,
                        "rebalancing_paused": False,
                        "operator_acknowledged_at": now,
                        "operator_acknowledged_by": "system_auto_resume",
                    }
                )
                self._emit_recovery_audit(
                    now=now,
                    current_equity=current_equity,
                    peak_equity=current_peak,
                    drawdown_pct=drawdown_pct,
                    config=config,
                    auto_resumed=True,
                )
                logger.info(
                    "PORTFOLIO_DRAWDOWN_RECOVERY",
                    extra={
                        "drawdown_pct": round(drawdown_pct, 6),
                        "threshold_pct": config.max_drawdown_pct,
                        "recovery_mode": config.recovery_mode.value,
                        "auto_resumed": True,
                    },
                )
            else:
                # MANUAL_RESUME_REQUIRED or RECOVERY_OBSERVATION:
                # governance remains paused; log recovery for visibility.
                logger.info(
                    "portfolio_drawdown.recovered_but_paused_pending_operator",
                    extra={
                        "drawdown_pct": round(drawdown_pct, 6),
                        "threshold_pct": config.max_drawdown_pct,
                        "recovery_mode": config.recovery_mode.value,
                        "action_required": "operator_must_call_acknowledge_breach",
                    },
                )

        elif breach_was_active and drawdown_pct > config.max_drawdown_pct:
            # Breach is ongoing — log at debug so logs are not flooded every cycle.
            logger.debug(
                "portfolio_drawdown.breach_ongoing",
                extra={
                    "drawdown_pct": round(drawdown_pct, 6),
                    "threshold_pct": config.max_drawdown_pct,
                    "drawdown_utilization": round(drawdown_utilization, 4),
                },
            )

        self._repo.update(**updates)

        # Read final state to construct the return value.
        final = self._repo.get_current_state()
        breach_active = (
            final.breach_detected_at is not None and final.operator_acknowledged_at is None
        )

        self._emit_metrics(
            drawdown_pct=drawdown_pct,
            peak_equity=current_peak,
            governance_paused=bool(final.governance_paused),
            newly_breached=newly_breached,
            kill_switch_activated=kill_switch_activated,
        )

        return PortfolioGovernanceEvaluation(
            current_equity=current_equity,
            peak_equity=current_peak,
            drawdown_pct=drawdown_pct,
            threshold_pct=config.max_drawdown_pct,
            drawdown_utilization=drawdown_utilization,
            breach_active=breach_active,
            newly_breached=newly_breached,
            trading_blocked=bool(final.governance_paused),
            rebalancing_blocked=bool(final.rebalancing_paused),
            triggered_action=final.triggered_action,
            recovery_mode=final.recovery_mode,
            kill_switch_activated=kill_switch_activated,
        )

    # ------------------------------------------------------------------
    # Read-only block checks (safe for high-frequency polling)
    # ------------------------------------------------------------------

    def is_trading_blocked(self) -> bool:
        """
        Return True if portfolio governance has paused new trading.

        Reads the persisted governance state without triggering a new
        evaluation.  Safe to call at any point in the cycle.
        """
        return bool(self._repo.get_current_state().governance_paused)

    def is_rebalancing_blocked(self) -> bool:
        """
        Return True if portfolio governance has paused rebalancing.

        Reads the persisted governance state without triggering a new
        evaluation.  Safe to call at any point in the cycle.
        """
        return bool(self._repo.get_current_state().rebalancing_paused)

    def get_state(self):
        """Return the raw governance state row for inspection or CLI display."""
        return self._repo.get_current_state()

    # ------------------------------------------------------------------
    # Operator acknowledgement
    # ------------------------------------------------------------------

    def acknowledge_breach(
        self,
        *,
        operator: str,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        """
        Operator acknowledges an active governance breach and requests resume.

        Clears governance_paused and rebalancing_paused, records the
        acknowledgement in the SOR, and emits a PORTFOLIO_GOVERNANCE_RESUME
        audit event.

        If there is no active breach (breach_detected_at is None or already
        acknowledged) this is a no-op — calling it repeatedly is safe.

        Note: if the drawdown is still above threshold when this is called,
        the very next evaluate() will detect a fresh breach and re-apply
        governance actions.  Operators should verify the portfolio equity has
        recovered before acknowledging.
        """
        if now is None:
            now = datetime.now(UTC)

        state = self._repo.get_current_state()
        if state.breach_detected_at is None:
            logger.info(
                "portfolio_governance.acknowledge_breach.no_active_breach",
                extra={"operator": operator},
            )
            return

        if state.operator_acknowledged_at is not None:
            logger.info(
                "portfolio_governance.acknowledge_breach.already_acknowledged",
                extra={
                    "operator": operator,
                    "previously_acknowledged_by": state.operator_acknowledged_by,
                },
            )
            return

        self._repo.update(
            governance_paused=False,
            rebalancing_paused=False,
            operator_acknowledged_at=now,
            operator_acknowledged_by=operator,
        )

        self._audit.record_operator_action(
            action="PORTFOLIO_GOVERNANCE_RESUME",
            actor=operator,
            reason=reason,
            occurred_at=now,
            metadata={
                "breach_detected_at": (
                    state.breach_detected_at.isoformat() if state.breach_detected_at else None
                ),
                "breach_drawdown_pct": state.breach_drawdown_pct,
                "breach_threshold_pct": state.breach_threshold_pct,
                "triggered_action": state.triggered_action,
                "kill_switch_auto_activated": state.kill_switch_auto_activated,
            },
            component="portfolio_governance",
        )

        logger.info(
            "PORTFOLIO_GOVERNANCE_RESUME",
            extra={
                "operator": operator,
                "reason": reason,
                "breach_drawdown_pct": state.breach_drawdown_pct,
            },
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _emit_breach_audit(
        self,
        *,
        now: datetime,
        current_equity: float,
        peak_equity: float,
        drawdown_pct: float,
        config: PortfolioDrawdownConfig,
        kill_switch_activated: bool,
        drawdown_utilization: float,
    ) -> None:
        self._audit.record_operator_action(
            action="PORTFOLIO_DRAWDOWN_BREACH",
            actor="system",
            reason=(
                f"Portfolio drawdown {drawdown_pct:.4f} exceeded threshold"
                f" {config.max_drawdown_pct:.4f}"
            ),
            occurred_at=now,
            metadata={
                "current_equity_usd": current_equity,
                "peak_equity_usd": peak_equity,
                "drawdown_pct": round(drawdown_pct, 6),
                "threshold_pct": config.max_drawdown_pct,
                "drawdown_utilization": round(drawdown_utilization, 4),
                "triggered_action": config.action.value,
                "recovery_mode": config.recovery_mode.value,
                "kill_switch_activated": kill_switch_activated,
            },
            component="portfolio_governance",
        )

    def _emit_governance_pause_audit(
        self,
        *,
        now: datetime,
        action: PortfolioDrawdownAction,
        drawdown_pct: float,
        threshold_pct: float,
    ) -> None:
        if action == PortfolioDrawdownAction.WARN_ONLY:
            return
        self._audit.record_operator_action(
            action="PORTFOLIO_GOVERNANCE_PAUSE",
            actor="system",
            reason=f"Governance pause triggered by {action.value}",
            occurred_at=now,
            metadata={
                "drawdown_pct": round(drawdown_pct, 6),
                "threshold_pct": threshold_pct,
                "action": action.value,
            },
            component="portfolio_governance",
        )

    def _emit_recovery_audit(
        self,
        *,
        now: datetime,
        current_equity: float,
        peak_equity: float,
        drawdown_pct: float,
        config: PortfolioDrawdownConfig,
        auto_resumed: bool,
    ) -> None:
        self._audit.record_operator_action(
            action="PORTFOLIO_DRAWDOWN_RECOVERY",
            actor="system" if auto_resumed else "operator",
            reason=f"Drawdown recovered to {drawdown_pct:.4f} (threshold {config.max_drawdown_pct:.4f})",
            occurred_at=now,
            metadata={
                "current_equity_usd": current_equity,
                "peak_equity_usd": peak_equity,
                "drawdown_pct": round(drawdown_pct, 6),
                "threshold_pct": config.max_drawdown_pct,
                "recovery_mode": config.recovery_mode.value,
                "auto_resumed": auto_resumed,
            },
            component="portfolio_governance",
        )

    def _emit_metrics(
        self,
        *,
        drawdown_pct: float,
        peak_equity: float,
        governance_paused: bool,
        newly_breached: bool,
        kill_switch_activated: bool,
    ) -> None:
        try:
            from autonomous_trading_platform.observability.metrics import (
                ratp_portfolio_drawdown_breach_total,
                ratp_portfolio_drawdown_pct,
                ratp_portfolio_governance_paused,
                ratp_portfolio_kill_switch_activations_total,
                ratp_portfolio_peak_equity,
            )

            ratp_portfolio_drawdown_pct.record(drawdown_pct)
            ratp_portfolio_peak_equity.record(peak_equity)
            ratp_portfolio_governance_paused.record(1.0 if governance_paused else 0.0)

            if newly_breached:
                ratp_portfolio_drawdown_breach_total.add(1)

            if kill_switch_activated:
                ratp_portfolio_kill_switch_activations_total.add(1)

        except Exception:
            # Metrics emission must never raise — it's purely observational.
            logger.debug("portfolio_governance.metrics_emission_failed", exc_info=True)
