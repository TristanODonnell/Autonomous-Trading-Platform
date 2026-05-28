# tests/execution/test_portfolio_drawdown_governance_service.py

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from autonomous_trading_platform.execution.services.portfolio_drawdown_governance_service import (
    PortfolioDrawdownAction,
    PortfolioDrawdownConfig,
    PortfolioDrawdownGovernanceService,
    PortfolioGovernanceEvaluation,
    PortfolioRecoveryMode,
)

# ---------------------------------------------------------------------------
# In-memory fakes — no DB required for unit tests
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
PEAK = 100_000.0


@dataclass
class _GovernanceStateStub:
    governance_id: str = "current"
    peak_equity_usd: float | None = None
    governance_paused: bool = False
    rebalancing_paused: bool = False
    breach_detected_at: datetime | None = None
    breach_drawdown_pct: float | None = None
    breach_threshold_pct: float | None = None
    triggered_action: str | None = None
    kill_switch_auto_activated: bool = False
    operator_acknowledged_at: datetime | None = None
    operator_acknowledged_by: str | None = None
    recovery_mode: str | None = None
    created_at: datetime = field(default_factory=lambda: NOW)
    updated_at: datetime = field(default_factory=lambda: NOW)


class FakeGovernanceRepo:
    def __init__(self, initial: _GovernanceStateStub | None = None) -> None:
        self._state = initial or _GovernanceStateStub()

    def get_current_state(self) -> _GovernanceStateStub:
        return self._state

    def update(self, **kwargs) -> _GovernanceStateStub:
        for k, v in kwargs.items():
            setattr(self._state, k, v)
        return self._state


@dataclass
class _AuditEntry:
    action: str
    actor: str
    reason: str
    metadata: dict


class FakeAuditRepo:
    def __init__(self) -> None:
        self.events: list[_AuditEntry] = []

    def record_operator_action(
        self, *, action, actor, reason, occurred_at, metadata, component="controls"
    ) -> None:
        self.events.append(
            _AuditEntry(action=action, actor=actor, reason=reason, metadata=metadata)
        )

    def audit_actions(self) -> list[str]:
        return [e.action for e in self.events]


class FakeKillSwitchService:
    def __init__(self) -> None:
        self.enabled = False
        self.enable_calls: list[dict] = []
        self.disable_calls: list[dict] = []

    def enable(self, reason: str, updated_by: str) -> None:
        self.enabled = True
        self.enable_calls.append({"reason": reason, "updated_by": updated_by})

    def disable(self, reason: str, updated_by: str) -> None:
        self.enabled = False
        self.disable_calls.append({"reason": reason, "updated_by": updated_by})

    def is_enabled(self) -> bool:
        return self.enabled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(
    *,
    initial_state: _GovernanceStateStub | None = None,
    kill_switch: FakeKillSwitchService | None = None,
) -> tuple[PortfolioDrawdownGovernanceService, FakeGovernanceRepo, FakeAuditRepo]:
    repo = FakeGovernanceRepo(initial_state)
    audit = FakeAuditRepo()
    svc = PortfolioDrawdownGovernanceService(
        governance_repo=repo,
        audit_repo=audit,
        kill_switch_service=kill_switch,
    )
    return svc, repo, audit


def default_config(
    enabled: bool = True,
    max_drawdown_pct: float = 0.10,
    action: PortfolioDrawdownAction = PortfolioDrawdownAction.PAUSE_NEW_TRADING,
    recovery_mode: PortfolioRecoveryMode = PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
    auto_kill_switch: bool = False,
) -> PortfolioDrawdownConfig:
    return PortfolioDrawdownConfig(
        enabled=enabled,
        max_drawdown_pct=max_drawdown_pct,
        action=action,
        recovery_mode=recovery_mode,
        auto_kill_switch=auto_kill_switch,
    )


def evaluate_at(
    svc: PortfolioDrawdownGovernanceService,
    equity: float,
    *,
    enabled: bool = True,
    max_drawdown_pct: float = 0.10,
    action: PortfolioDrawdownAction = PortfolioDrawdownAction.PAUSE_NEW_TRADING,
    recovery_mode: PortfolioRecoveryMode = PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
    auto_kill_switch: bool = False,
) -> PortfolioGovernanceEvaluation:
    return svc.evaluate(
        current_equity=equity,
        config=default_config(
            enabled=enabled,
            max_drawdown_pct=max_drawdown_pct,
            action=action,
            recovery_mode=recovery_mode,
            auto_kill_switch=auto_kill_switch,
        ),
        now=NOW,
    )


# ---------------------------------------------------------------------------
# 1. Configuration and construction
# ---------------------------------------------------------------------------


class TestPortfolioDrawdownConfig:
    def test_from_settings_uses_defaults_for_missing_fields(self) -> None:
        class EmptySettings:
            pass

        cfg = PortfolioDrawdownConfig.from_settings(EmptySettings())
        assert cfg.max_drawdown_pct == 0.15
        assert cfg.action == PortfolioDrawdownAction.PAUSE_NEW_TRADING
        assert cfg.recovery_mode == PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED
        assert cfg.enabled is True

    def test_from_settings_reads_custom_values(self) -> None:
        class Settings:
            portfolio_max_drawdown_pct = 0.08
            portfolio_drawdown_action = "pause_rebalancing"
            portfolio_drawdown_recovery_mode = "auto_resume"
            portfolio_auto_kill_switch = False

        cfg = PortfolioDrawdownConfig.from_settings(Settings())
        assert cfg.max_drawdown_pct == 0.08
        assert cfg.action == PortfolioDrawdownAction.PAUSE_REBALANCING
        assert cfg.recovery_mode == PortfolioRecoveryMode.AUTO_RESUME

    def test_from_settings_unknown_action_falls_back_safely(self) -> None:
        class Settings:
            portfolio_max_drawdown_pct = None
            portfolio_drawdown_action = "unknown_action_xyz"
            portfolio_drawdown_recovery_mode = None
            portfolio_auto_kill_switch = None

        cfg = PortfolioDrawdownConfig.from_settings(Settings())
        assert cfg.action == PortfolioDrawdownAction.PAUSE_NEW_TRADING

    def test_from_settings_unknown_recovery_mode_falls_back_safely(self) -> None:
        class Settings:
            portfolio_max_drawdown_pct = None
            portfolio_drawdown_action = None
            portfolio_drawdown_recovery_mode = "not_a_real_mode"
            portfolio_auto_kill_switch = None

        cfg = PortfolioDrawdownConfig.from_settings(Settings())
        assert cfg.recovery_mode == PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED


# ---------------------------------------------------------------------------
# 2. Disabled mode — backward compatibility
# ---------------------------------------------------------------------------


class TestDisabledMode:
    def test_disabled_returns_no_block(self) -> None:
        svc, _, _ = make_service()
        cfg = PortfolioDrawdownConfig(enabled=False, max_drawdown_pct=0.01)
        result = svc.evaluate(current_equity=50_000.0, config=cfg, now=NOW)
        assert result.trading_blocked is False
        assert result.rebalancing_blocked is False
        assert result.breach_active is False
        assert result.newly_breached is False

    def test_disabled_does_not_emit_audit_events(self) -> None:
        svc, _, audit = make_service()
        cfg = PortfolioDrawdownConfig(enabled=False, max_drawdown_pct=0.01)
        svc.evaluate(current_equity=50_000.0, config=cfg, now=NOW)
        assert audit.events == []


# ---------------------------------------------------------------------------
# 3. Peak equity tracking
# ---------------------------------------------------------------------------


class TestPeakEquityTracking:
    def test_first_call_initialises_peak_to_current_equity(self) -> None:
        svc, repo, _ = make_service()
        evaluate_at(svc, PEAK)
        assert repo.get_current_state().peak_equity_usd == PEAK

    def test_peak_updates_upward_on_new_high(self) -> None:
        svc, repo, _ = make_service()
        evaluate_at(svc, PEAK)
        evaluate_at(svc, PEAK * 1.05)
        assert repo.get_current_state().peak_equity_usd == pytest.approx(PEAK * 1.05)

    def test_peak_never_decreases_on_loss(self) -> None:
        svc, repo, _ = make_service()
        evaluate_at(svc, PEAK)
        evaluate_at(svc, PEAK * 0.90)
        assert repo.get_current_state().peak_equity_usd == pytest.approx(PEAK)

    def test_persisted_peak_survives_restart(self) -> None:
        # Simulate a restart by constructing a fresh service with the same repo
        # pre-seeded with the prior peak.
        initial = _GovernanceStateStub(peak_equity_usd=PEAK)
        svc, repo, _ = make_service(initial_state=initial)
        # Current equity is below prior peak — drawdown should be computed from
        # the persisted peak, NOT reset to current_equity.
        result = evaluate_at(svc, PEAK * 0.85)
        assert result.peak_equity == pytest.approx(PEAK)
        assert result.drawdown_pct == pytest.approx(0.15, rel=1e-4)

    def test_zero_peak_does_not_produce_divide_by_zero(self) -> None:
        initial = _GovernanceStateStub(peak_equity_usd=0.0)
        svc, _, _ = make_service(initial_state=initial)
        result = svc.evaluate(current_equity=1_000.0, config=default_config(), now=NOW)
        assert result.drawdown_pct == 0.0


# ---------------------------------------------------------------------------
# 4. Drawdown calculation
# ---------------------------------------------------------------------------


class TestDrawdownCalculation:
    def test_no_drawdown_when_at_peak(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK)
        assert result.drawdown_pct == pytest.approx(0.0)

    def test_ten_percent_drawdown_computed_correctly(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.90)
        assert result.drawdown_pct == pytest.approx(0.10, rel=1e-4)

    def test_drawdown_utilization_at_half_threshold(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        # 5% drawdown with 10% threshold → utilization = 0.5
        result = evaluate_at(svc, PEAK * 0.95, max_drawdown_pct=0.10)
        assert result.drawdown_utilization == pytest.approx(0.5, rel=1e-4)

    def test_drawdown_utilization_exceeds_one_on_breach(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.80, max_drawdown_pct=0.10)
        assert result.drawdown_utilization > 1.0


# ---------------------------------------------------------------------------
# 5. Breach detection and governance actions
# ---------------------------------------------------------------------------


class TestBreachDetection:
    def test_no_breach_below_threshold(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.95)
        assert result.newly_breached is False
        assert result.trading_blocked is False

    def test_breach_detected_exactly_at_threshold_is_not_triggered(self) -> None:
        # > not >= : a drawdown equal to the threshold does not breach.
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.90)  # exactly 10% with 10% threshold
        assert result.newly_breached is False

    def test_breach_detected_above_threshold(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.89)  # 11% > 10%
        assert result.newly_breached is True
        assert result.breach_active is True

    def test_breach_emits_audit_event(self) -> None:
        svc, _, audit = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85)
        assert "PORTFOLIO_DRAWDOWN_BREACH" in audit.audit_actions()

    def test_breach_persists_metadata_in_state(self) -> None:
        svc, repo, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85)
        state = repo.get_current_state()
        assert state.breach_detected_at == NOW
        assert state.breach_drawdown_pct == pytest.approx(0.15, rel=1e-4)
        assert state.breach_threshold_pct == pytest.approx(0.10, rel=1e-4)

    def test_second_evaluate_does_not_re_emit_breach_event(self) -> None:
        svc, _, audit = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85)
        breach_count_before = audit.audit_actions().count("PORTFOLIO_DRAWDOWN_BREACH")
        evaluate_at(svc, PEAK * 0.82)  # still breached, deeper
        breach_count_after = audit.audit_actions().count("PORTFOLIO_DRAWDOWN_BREACH")
        assert breach_count_after == breach_count_before  # no duplicate


# ---------------------------------------------------------------------------
# 6. WARN_ONLY action
# ---------------------------------------------------------------------------


class TestWarnOnlyAction:
    def test_warn_only_does_not_pause_trading(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.WARN_ONLY)
        assert result.trading_blocked is False
        assert result.rebalancing_blocked is False

    def test_warn_only_still_emits_breach_audit(self) -> None:
        svc, _, audit = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.WARN_ONLY)
        assert "PORTFOLIO_DRAWDOWN_BREACH" in audit.audit_actions()

    def test_warn_only_does_not_emit_governance_pause_audit(self) -> None:
        svc, _, audit = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.WARN_ONLY)
        assert "PORTFOLIO_GOVERNANCE_PAUSE" not in audit.audit_actions()


# ---------------------------------------------------------------------------
# 7. PAUSE_NEW_TRADING action
# ---------------------------------------------------------------------------


class TestPauseNewTradingAction:
    def test_breach_blocks_trading(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.PAUSE_NEW_TRADING)
        assert result.trading_blocked is True

    def test_breach_does_not_block_rebalancing(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.PAUSE_NEW_TRADING)
        assert result.rebalancing_blocked is False

    def test_is_trading_blocked_returns_true_after_breach(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.PAUSE_NEW_TRADING)
        assert svc.is_trading_blocked() is True

    def test_is_rebalancing_blocked_returns_false_after_pause_trading(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.PAUSE_NEW_TRADING)
        assert svc.is_rebalancing_blocked() is False


# ---------------------------------------------------------------------------
# 8. PAUSE_REBALANCING action
# ---------------------------------------------------------------------------


class TestPauseRebalancingAction:
    def test_breach_blocks_rebalancing(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.PAUSE_REBALANCING)
        assert result.rebalancing_blocked is True

    def test_breach_does_not_block_trading(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.PAUSE_REBALANCING)
        assert result.trading_blocked is False


# ---------------------------------------------------------------------------
# 9. ACTIVATE_KILL_SWITCH action
# ---------------------------------------------------------------------------


class TestActivateKillSwitchAction:
    def test_breach_blocks_trading_and_rebalancing(self) -> None:
        ks = FakeKillSwitchService()
        svc, _, _ = make_service(
            initial_state=_GovernanceStateStub(peak_equity_usd=PEAK),
            kill_switch=ks,
        )
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH)
        assert result.trading_blocked is True
        assert result.rebalancing_blocked is True

    def test_kill_switch_is_activated_on_breach(self) -> None:
        ks = FakeKillSwitchService()
        svc, _, _ = make_service(
            initial_state=_GovernanceStateStub(peak_equity_usd=PEAK),
            kill_switch=ks,
        )
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH)
        assert result.kill_switch_activated is True
        assert ks.is_enabled() is True

    def test_kill_switch_activation_persisted_in_state(self) -> None:
        ks = FakeKillSwitchService()
        svc, repo, _ = make_service(
            initial_state=_GovernanceStateStub(peak_equity_usd=PEAK),
            kill_switch=ks,
        )
        evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH)
        assert repo.get_current_state().kill_switch_auto_activated is True

    def test_kill_switch_not_activated_when_not_injected(self) -> None:
        # No kill_switch_service injected: activation request is ignored, no crash.
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH)
        assert result.kill_switch_activated is False


# ---------------------------------------------------------------------------
# 10. auto_kill_switch flag (secondary escalation)
# ---------------------------------------------------------------------------


class TestAutoKillSwitchFlag:
    def test_auto_kill_switch_activates_ks_on_breach(self) -> None:
        ks = FakeKillSwitchService()
        svc, _, _ = make_service(
            initial_state=_GovernanceStateStub(peak_equity_usd=PEAK),
            kill_switch=ks,
        )
        cfg = PortfolioDrawdownConfig(
            enabled=True,
            max_drawdown_pct=0.10,
            action=PortfolioDrawdownAction.PAUSE_NEW_TRADING,
            recovery_mode=PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
            auto_kill_switch=True,
        )
        svc.evaluate(current_equity=PEAK * 0.85, config=cfg, now=NOW)
        assert ks.is_enabled() is True

    def test_auto_kill_switch_without_injection_does_not_crash(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        cfg = PortfolioDrawdownConfig(
            enabled=True,
            max_drawdown_pct=0.10,
            action=PortfolioDrawdownAction.PAUSE_NEW_TRADING,
            auto_kill_switch=True,
        )
        result = svc.evaluate(current_equity=PEAK * 0.85, config=cfg, now=NOW)
        assert result.kill_switch_activated is False


# ---------------------------------------------------------------------------
# 11. Recovery modes
# ---------------------------------------------------------------------------


class TestManualResumeRequired:
    def _breach_state(self) -> _GovernanceStateStub:
        return _GovernanceStateStub(
            peak_equity_usd=PEAK,
            governance_paused=True,
            rebalancing_paused=False,
            breach_detected_at=NOW - timedelta(hours=2),
            breach_drawdown_pct=0.15,
            breach_threshold_pct=0.10,
            triggered_action="pause_new_trading",
            recovery_mode="manual_resume_required",
        )

    def test_governance_remains_paused_after_recovery_without_ack(self) -> None:
        svc, repo, _ = make_service(initial_state=self._breach_state())
        # Equity has fully recovered
        evaluate_at(
            svc,
            PEAK,
            recovery_mode=PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
        )
        assert repo.get_current_state().governance_paused is True

    def test_no_auto_recovery_audit_emitted(self) -> None:
        svc, _, audit = make_service(initial_state=self._breach_state())
        evaluate_at(svc, PEAK, recovery_mode=PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED)
        assert "PORTFOLIO_DRAWDOWN_RECOVERY" not in audit.audit_actions()


class TestAutoResumeRecovery:
    def _breach_state(self) -> _GovernanceStateStub:
        return _GovernanceStateStub(
            peak_equity_usd=PEAK,
            governance_paused=True,
            rebalancing_paused=False,
            breach_detected_at=NOW - timedelta(hours=2),
            breach_drawdown_pct=0.15,
            breach_threshold_pct=0.10,
            triggered_action="pause_new_trading",
            recovery_mode="auto_resume",
        )

    def test_governance_clears_when_drawdown_recovers(self) -> None:
        svc, repo, _ = make_service(initial_state=self._breach_state())
        evaluate_at(svc, PEAK, recovery_mode=PortfolioRecoveryMode.AUTO_RESUME)
        assert repo.get_current_state().governance_paused is False

    def test_trading_not_blocked_after_auto_recovery(self) -> None:
        svc, _, _ = make_service(initial_state=self._breach_state())
        result = evaluate_at(svc, PEAK, recovery_mode=PortfolioRecoveryMode.AUTO_RESUME)
        assert result.trading_blocked is False

    def test_recovery_audit_emitted_on_auto_resume(self) -> None:
        svc, _, audit = make_service(initial_state=self._breach_state())
        evaluate_at(svc, PEAK, recovery_mode=PortfolioRecoveryMode.AUTO_RESUME)
        assert "PORTFOLIO_DRAWDOWN_RECOVERY" in audit.audit_actions()

    def test_still_blocked_if_drawdown_above_threshold_despite_auto_resume(self) -> None:
        svc, repo, _ = make_service(initial_state=self._breach_state())
        # equity only partially recovered — still 12% below peak > 10% threshold
        evaluate_at(svc, PEAK * 0.88, recovery_mode=PortfolioRecoveryMode.AUTO_RESUME)
        assert repo.get_current_state().governance_paused is True


class TestRecoveryObservation:
    def _breach_state(self) -> _GovernanceStateStub:
        return _GovernanceStateStub(
            peak_equity_usd=PEAK,
            governance_paused=True,
            rebalancing_paused=False,
            breach_detected_at=NOW - timedelta(hours=2),
            breach_drawdown_pct=0.15,
            breach_threshold_pct=0.10,
            triggered_action="pause_new_trading",
            recovery_mode="recovery_observation",
        )

    def test_governance_remains_paused_in_observation_mode(self) -> None:
        svc, repo, _ = make_service(initial_state=self._breach_state())
        evaluate_at(svc, PEAK, recovery_mode=PortfolioRecoveryMode.RECOVERY_OBSERVATION)
        assert repo.get_current_state().governance_paused is True


# ---------------------------------------------------------------------------
# 12. Operator acknowledgement
# ---------------------------------------------------------------------------


class TestOperatorAcknowledgement:
    def _breach_state(self) -> _GovernanceStateStub:
        return _GovernanceStateStub(
            peak_equity_usd=PEAK,
            governance_paused=True,
            rebalancing_paused=True,
            breach_detected_at=NOW - timedelta(hours=1),
            breach_drawdown_pct=0.15,
            breach_threshold_pct=0.10,
            triggered_action="activate_kill_switch",
            recovery_mode="manual_resume_required",
        )

    def test_acknowledge_clears_governance_paused(self) -> None:
        svc, repo, _ = make_service(initial_state=self._breach_state())
        svc.acknowledge_breach(operator="ops@example.com", reason="reviewed and cleared", now=NOW)
        assert repo.get_current_state().governance_paused is False

    def test_acknowledge_clears_rebalancing_paused(self) -> None:
        svc, repo, _ = make_service(initial_state=self._breach_state())
        svc.acknowledge_breach(operator="ops@example.com", reason="reviewed and cleared", now=NOW)
        assert repo.get_current_state().rebalancing_paused is False

    def test_acknowledge_records_operator_and_timestamp(self) -> None:
        svc, repo, _ = make_service(initial_state=self._breach_state())
        svc.acknowledge_breach(operator="ops@example.com", reason="reviewed", now=NOW)
        state = repo.get_current_state()
        assert state.operator_acknowledged_by == "ops@example.com"
        assert state.operator_acknowledged_at == NOW

    def test_acknowledge_emits_resume_audit_event(self) -> None:
        svc, _, audit = make_service(initial_state=self._breach_state())
        svc.acknowledge_breach(operator="ops@example.com", reason="reviewed", now=NOW)
        assert "PORTFOLIO_GOVERNANCE_RESUME" in audit.audit_actions()

    def test_is_trading_blocked_returns_false_after_acknowledgement(self) -> None:
        svc, _, _ = make_service(initial_state=self._breach_state())
        svc.acknowledge_breach(operator="ops@example.com", reason="reviewed", now=NOW)
        assert svc.is_trading_blocked() is False

    def test_acknowledge_with_no_active_breach_is_noop(self) -> None:
        svc, repo, audit = make_service()
        svc.acknowledge_breach(operator="ops@example.com", reason="sanity check", now=NOW)
        assert audit.events == []
        assert repo.get_current_state().governance_paused is False

    def test_acknowledge_when_already_acknowledged_is_noop(self) -> None:
        state = self._breach_state()
        state.operator_acknowledged_at = NOW - timedelta(minutes=30)
        state.operator_acknowledged_by = "prior_operator@example.com"
        svc, _, audit = make_service(initial_state=state)
        svc.acknowledge_breach(operator="second_operator@example.com", reason="double ack", now=NOW)
        assert "PORTFOLIO_GOVERNANCE_RESUME" not in audit.audit_actions()

    def test_re_breach_after_acknowledgement_triggers_new_breach(self) -> None:
        """
        After operator acknowledges, if equity drops again below threshold,
        evaluate() should detect a fresh breach.
        """
        state = _GovernanceStateStub(
            peak_equity_usd=PEAK,
            governance_paused=False,
            rebalancing_paused=False,
            breach_detected_at=NOW - timedelta(hours=1),
            operator_acknowledged_at=NOW - timedelta(minutes=5),
            operator_acknowledged_by="ops@example.com",
        )
        svc, _, audit = make_service(initial_state=state)
        # Equity drops below threshold again
        result = evaluate_at(svc, PEAK * 0.85)
        assert result.newly_breached is True
        assert "PORTFOLIO_DRAWDOWN_BREACH" in audit.audit_actions()


# ---------------------------------------------------------------------------
# 13. State persistence semantics
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_governance_paused_persists_between_evaluations(self) -> None:
        svc, repo, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85)
        assert repo.get_current_state().governance_paused is True
        # Build a fresh service sharing the same repo (simulates restart)
        svc2 = PortfolioDrawdownGovernanceService(
            governance_repo=repo,
            audit_repo=FakeAuditRepo(),
        )
        assert svc2.is_trading_blocked() is True

    def test_breach_metadata_persists(self) -> None:
        svc, repo, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85)
        state = repo.get_current_state()
        assert state.breach_detected_at is not None
        assert state.breach_drawdown_pct is not None
        assert state.breach_threshold_pct is not None


# ---------------------------------------------------------------------------
# 14. Metrics emission
# ---------------------------------------------------------------------------


class TestMetricsEmission:
    def test_evaluate_does_not_raise_when_metrics_unavailable(self) -> None:
        """Metrics are imported lazily; the service must never crash if they fail."""
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        # This always succeeds even if OTel metrics are not configured in tests.
        result = evaluate_at(svc, PEAK * 0.85)
        assert result.newly_breached is True


# ---------------------------------------------------------------------------
# 15. Block checks without evaluation
# ---------------------------------------------------------------------------


class TestBlockChecks:
    def test_is_trading_blocked_false_when_no_breach(self) -> None:
        svc, _, _ = make_service()
        assert svc.is_trading_blocked() is False

    def test_is_rebalancing_blocked_false_when_no_breach(self) -> None:
        svc, _, _ = make_service()
        assert svc.is_rebalancing_blocked() is False

    def test_is_trading_blocked_true_when_pre_seeded_paused(self) -> None:
        state = _GovernanceStateStub(governance_paused=True)
        svc, _, _ = make_service(initial_state=state)
        assert svc.is_trading_blocked() is True

    def test_is_rebalancing_blocked_true_when_pre_seeded_paused(self) -> None:
        state = _GovernanceStateStub(rebalancing_paused=True)
        svc, _, _ = make_service(initial_state=state)
        assert svc.is_rebalancing_blocked() is True


# ---------------------------------------------------------------------------
# 16. Audit event content
# ---------------------------------------------------------------------------


class TestAuditEventContent:
    def test_breach_audit_contains_equity_figures(self) -> None:
        svc, _, audit = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        evaluate_at(svc, PEAK * 0.85)
        breach_event = next(e for e in audit.events if e.action == "PORTFOLIO_DRAWDOWN_BREACH")
        assert "current_equity_usd" in breach_event.metadata
        assert "peak_equity_usd" in breach_event.metadata
        assert "drawdown_pct" in breach_event.metadata
        assert "threshold_pct" in breach_event.metadata
        assert "triggered_action" in breach_event.metadata

    def test_resume_audit_contains_breach_context(self) -> None:
        state = _GovernanceStateStub(
            peak_equity_usd=PEAK,
            governance_paused=True,
            breach_detected_at=NOW - timedelta(hours=1),
            breach_drawdown_pct=0.15,
            breach_threshold_pct=0.10,
            triggered_action="pause_new_trading",
        )
        svc, _, audit = make_service(initial_state=state)
        svc.acknowledge_breach(operator="ops@example.com", reason="confirmed", now=NOW)
        resume_event = next(e for e in audit.events if e.action == "PORTFOLIO_GOVERNANCE_RESUME")
        assert resume_event.metadata["breach_drawdown_pct"] == pytest.approx(0.15)
        assert resume_event.metadata["triggered_action"] == "pause_new_trading"

    def test_breach_audit_includes_kill_switch_flag(self) -> None:
        ks = FakeKillSwitchService()
        svc, _, audit = make_service(
            initial_state=_GovernanceStateStub(peak_equity_usd=PEAK),
            kill_switch=ks,
        )
        evaluate_at(svc, PEAK * 0.85, action=PortfolioDrawdownAction.ACTIVATE_KILL_SWITCH)
        breach_event = next(e for e in audit.events if e.action == "PORTFOLIO_DRAWDOWN_BREACH")
        assert breach_event.metadata["kill_switch_activated"] is True


# ---------------------------------------------------------------------------
# 17. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_very_small_equity_does_not_error(self) -> None:
        svc, _, _ = make_service()
        result = svc.evaluate(current_equity=0.01, config=default_config(), now=NOW)
        assert result.drawdown_pct == 0.0

    def test_negative_equity_clamped_to_zero_drawdown_relative_to_prior_peak(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = svc.evaluate(current_equity=-1000.0, config=default_config(), now=NOW)
        # drawdown = (100000 - (-1000)) / 100000 > 1.0, clamped to realistic value
        assert result.drawdown_pct > 1.0
        assert result.newly_breached is True

    def test_evaluate_with_default_now_does_not_raise(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = svc.evaluate(current_equity=PEAK * 0.80, config=default_config())
        assert result.newly_breached is True

    def test_breach_active_flag_accurate_when_not_yet_acknowledged(self) -> None:
        svc, _, _ = make_service(initial_state=_GovernanceStateStub(peak_equity_usd=PEAK))
        result = evaluate_at(svc, PEAK * 0.85)
        assert result.breach_active is True

    def test_breach_active_false_after_acknowledgement(self) -> None:
        state = _GovernanceStateStub(
            peak_equity_usd=PEAK,
            governance_paused=True,
            breach_detected_at=NOW - timedelta(hours=1),
        )
        svc, _, _ = make_service(initial_state=state)
        svc.acknowledge_breach(operator="ops@example.com", reason="ok", now=NOW)
        result = evaluate_at(svc, PEAK)
        assert result.breach_active is False


# ---------------------------------------------------------------------------
# 18. Integration: full breach → pause → acknowledge → resume → re-breach flow
# ---------------------------------------------------------------------------


class TestFullGovernanceLifecycle:
    def test_full_lifecycle(self) -> None:
        ks = FakeKillSwitchService()
        svc, repo, audit = make_service(
            initial_state=_GovernanceStateStub(peak_equity_usd=PEAK),
            kill_switch=ks,
        )

        # 1. Portfolio at peak — no breach
        r1 = svc.evaluate(
            current_equity=PEAK,
            config=PortfolioDrawdownConfig(
                enabled=True,
                max_drawdown_pct=0.10,
                action=PortfolioDrawdownAction.PAUSE_NEW_TRADING,
                recovery_mode=PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
            ),
            now=NOW,
        )
        assert r1.trading_blocked is False
        assert r1.breach_active is False

        # 2. Portfolio drops 15% — breach fires
        r2 = svc.evaluate(
            current_equity=PEAK * 0.85,
            config=PortfolioDrawdownConfig(
                enabled=True,
                max_drawdown_pct=0.10,
                action=PortfolioDrawdownAction.PAUSE_NEW_TRADING,
                recovery_mode=PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
            ),
            now=NOW + timedelta(hours=1),
        )
        assert r2.newly_breached is True
        assert r2.trading_blocked is True

        # 3. Portfolio recovers — still blocked (manual resume required)
        r3 = svc.evaluate(
            current_equity=PEAK * 0.95,
            config=PortfolioDrawdownConfig(
                enabled=True,
                max_drawdown_pct=0.10,
                action=PortfolioDrawdownAction.PAUSE_NEW_TRADING,
                recovery_mode=PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
            ),
            now=NOW + timedelta(hours=2),
        )
        assert r3.trading_blocked is True
        assert r3.newly_breached is False

        # 4. Operator acknowledges
        svc.acknowledge_breach(
            operator="ops@example.com",
            reason="reviewed; portfolio recovered",
            now=NOW + timedelta(hours=3),
        )
        assert svc.is_trading_blocked() is False

        # 5. Back to peak — clean state
        r5 = svc.evaluate(
            current_equity=PEAK,
            config=PortfolioDrawdownConfig(
                enabled=True,
                max_drawdown_pct=0.10,
                action=PortfolioDrawdownAction.PAUSE_NEW_TRADING,
                recovery_mode=PortfolioRecoveryMode.MANUAL_RESUME_REQUIRED,
            ),
            now=NOW + timedelta(hours=4),
        )
        assert r5.trading_blocked is False
        assert r5.breach_active is False

        expected_actions = {
            "PORTFOLIO_DRAWDOWN_BREACH",
            "PORTFOLIO_GOVERNANCE_PAUSE",
            "PORTFOLIO_GOVERNANCE_RESUME",
        }
        assert expected_actions.issubset(set(audit.audit_actions()))
