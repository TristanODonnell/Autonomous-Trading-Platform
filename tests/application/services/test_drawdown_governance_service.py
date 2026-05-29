from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.drawdown_governance_service import (
    DrawdownGovernanceService,
    apply_transition_rules,
    compute_drawdown_utilization,
    compute_target_ladder_state,
)
from autonomous_trading_platform.contracts.governance.drawdown_governance import (
    LADDER_ALLOCATION_SCALARS,
    LADDER_HEALTH_SUGGESTIONS,
    DrawdownGovernanceLadderConfig,
    DrawdownGovernanceState,
    ladder_severity,
    ladder_state_one_step_better,
)
from autonomous_trading_platform.storage.sor.models.drawdown_governance_ladder_state import (
    DrawdownGovernanceLadderStateRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_state_repository import (
    DrawdownGovernanceLadderStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_transition_repository import (
    DrawdownGovernanceLadderTransitionRepository,
)

# ---------------------------------------------------------------------------
# Shared config objects
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)

_ENFORCE_CONFIG = DrawdownGovernanceLadderConfig(
    enabled=True,
    mode="enforce",
    warning_threshold=0.50,
    probation_threshold=0.75,
    suspension_threshold=0.90,
    breach_threshold=1.00,
    normal_allocation_scalar=1.00,
    warning_allocation_scalar=0.50,
    probation_allocation_scalar=0.25,
    suspended_allocation_scalar=0.00,
    breached_allocation_scalar=0.00,
    hysteresis_band=0.05,
    cooldown_after_warning_hours=6.0,
    cooldown_after_probation_hours=12.0,
    cooldown_after_suspension_hours=24.0,
    cooldown_after_breach_hours=48.0,
    min_observation_cycles=2,
    breach_requires_operator_ack=True,
    auto_demote_on_breach=False,
)

_OBSERVE_CONFIG = DrawdownGovernanceLadderConfig(
    enabled=True,
    mode="observe",
)

_DISABLED_CONFIG = DrawdownGovernanceLadderConfig(enabled=False)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_strategy(
    session: Session,
    strategy_id: str,
    state: str = "approved_live",
) -> StrategyGovernance:
    now = datetime.now(UTC)
    session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            config_json={"strategy_id": strategy_id},
            created_at=now,
            strategy_type="test",
            metadata_json={},
        )
    )
    gov = StrategyGovernance(
        strategy_id=strategy_id,
        config_hash=f"{strategy_id}_hash",
        current_state=state,
        experiment_id="test_exp",
        source_run_id=None,
        submitted_at=now,
        updated_at=now,
        submitted_by="test",
    )
    session.add(gov)
    session.flush()
    return gov


def _seed_live_snapshot(
    session: Session,
    strategy_id: str,
    realized_drawdown: float,
) -> None:
    session.add(
        StrategyLivePerformanceSnapshot(
            snapshot_id=str(uuid4()),
            strategy_id=strategy_id,
            computed_at=datetime.now(UTC),
            days_live=30,
            trade_count=50,
            realized_drawdown=realized_drawdown,
            realized_return=None,
            realized_volatility=None,
            rolling_sharpe=None,
            live_win_rate=None,
        )
    )
    session.flush()


def _seed_operator_settings(
    session: Session,
    max_strategy_drawdown: float = 0.12,
) -> None:
    from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
        OperatorSettingsRepository,
    )

    repo = OperatorSettingsRepository(session)
    row = repo.get_or_create_default()
    row.max_strategy_drawdown = max_strategy_drawdown
    session.flush()


def _seed_ladder_state(
    session: Session,
    strategy_id: str,
    ladder_state: str = "normal",
    evaluation_count: int = 3,
    cooldown_expires_at: datetime | None = None,
    operator_ack_required: bool = False,
    drawdown_utilization: float | None = None,
) -> DrawdownGovernanceLadderStateRow:
    now = _NOW
    row = DrawdownGovernanceLadderStateRow(
        ladder_state_id=str(uuid4()),
        strategy_id=strategy_id,
        ladder_state=ladder_state,
        previous_ladder_state=None,
        transition_reason="seed",
        realized_drawdown=0.04,
        max_drawdown_allowed=0.08,
        drawdown_utilization=drawdown_utilization,
        allocation_scalar=1.0,
        cooldown_expires_at=cooldown_expires_at,
        evaluation_count=evaluation_count,
        operator_ack_required=operator_ack_required,
        operator_acknowledged_at=None,
        operator_acknowledged_by=None,
        last_transition_at=now,
        last_evaluated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


# ===========================================================================
# Unit tests — pure helper functions (no DB)
# ===========================================================================


class TestComputeDrawdownUtilization:
    def test_normal_case(self):
        assert compute_drawdown_utilization(0.04, 0.08) == pytest.approx(0.50)

    def test_zero_drawdown(self):
        assert compute_drawdown_utilization(0.0, 0.10) == pytest.approx(0.0)

    def test_fully_breached(self):
        assert compute_drawdown_utilization(0.10, 0.10) == pytest.approx(1.00)

    def test_beyond_limit(self):
        assert compute_drawdown_utilization(0.12, 0.10) == pytest.approx(1.20)

    def test_zero_max_drawdown_returns_one(self):
        assert compute_drawdown_utilization(0.05, 0.0) == pytest.approx(1.0)


class TestComputeTargetLadderState:
    def test_normal_below_warning(self):
        state, reason = compute_target_ladder_state(0.49, _ENFORCE_CONFIG)
        assert state == DrawdownGovernanceState.NORMAL
        assert "normal range" in reason

    def test_warning_at_threshold(self):
        state, _ = compute_target_ladder_state(0.50, _ENFORCE_CONFIG)
        assert state == DrawdownGovernanceState.WARNING

    def test_warning_within_band(self):
        state, _ = compute_target_ladder_state(0.60, _ENFORCE_CONFIG)
        assert state == DrawdownGovernanceState.WARNING

    def test_probation_at_threshold(self):
        state, _ = compute_target_ladder_state(0.75, _ENFORCE_CONFIG)
        assert state == DrawdownGovernanceState.PROBATION

    def test_suspension_at_threshold(self):
        state, _ = compute_target_ladder_state(0.90, _ENFORCE_CONFIG)
        assert state == DrawdownGovernanceState.SUSPENDED

    def test_breach_at_limit(self):
        state, _ = compute_target_ladder_state(1.00, _ENFORCE_CONFIG)
        assert state == DrawdownGovernanceState.BREACHED

    def test_breach_beyond_limit(self):
        state, _ = compute_target_ladder_state(1.25, _ENFORCE_CONFIG)
        assert state == DrawdownGovernanceState.BREACHED

    def test_reason_contains_utilization(self):
        _, reason = compute_target_ladder_state(0.80, _ENFORCE_CONFIG)
        assert "80.00%" in reason


class TestLadderSeverityOrdering:
    def test_severity_order(self):
        states = [
            DrawdownGovernanceState.NORMAL,
            DrawdownGovernanceState.WARNING,
            DrawdownGovernanceState.PROBATION,
            DrawdownGovernanceState.SUSPENDED,
            DrawdownGovernanceState.BREACHED,
        ]
        severities = [ladder_severity(s) for s in states]
        assert severities == sorted(severities)

    def test_one_step_better_from_warning(self):
        assert (
            ladder_state_one_step_better(DrawdownGovernanceState.WARNING)
            == DrawdownGovernanceState.NORMAL
        )

    def test_one_step_better_from_breached(self):
        assert (
            ladder_state_one_step_better(DrawdownGovernanceState.BREACHED)
            == DrawdownGovernanceState.SUSPENDED
        )

    def test_one_step_better_from_normal(self):
        assert (
            ladder_state_one_step_better(DrawdownGovernanceState.NORMAL)
            == DrawdownGovernanceState.NORMAL
        )


class TestLadderAllocationScalars:
    def test_normal_full_allocation(self):
        assert LADDER_ALLOCATION_SCALARS[DrawdownGovernanceState.NORMAL] == 1.00

    def test_warning_half_allocation(self):
        assert LADDER_ALLOCATION_SCALARS[DrawdownGovernanceState.WARNING] == 0.50

    def test_probation_quarter_allocation(self):
        assert LADDER_ALLOCATION_SCALARS[DrawdownGovernanceState.PROBATION] == 0.25

    def test_suspended_zero_allocation(self):
        assert LADDER_ALLOCATION_SCALARS[DrawdownGovernanceState.SUSPENDED] == 0.00

    def test_breached_zero_allocation(self):
        assert LADDER_ALLOCATION_SCALARS[DrawdownGovernanceState.BREACHED] == 0.00


class TestLadderHealthSuggestions:
    def test_normal_maps_to_healthy(self):
        assert LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.NORMAL] == "healthy"

    def test_warning_maps_to_watch(self):
        assert LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.WARNING] == "watch"

    def test_probation_maps_to_degrading(self):
        assert LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.PROBATION] == "degrading"

    def test_suspended_maps_to_critical(self):
        assert LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.SUSPENDED] == "critical"

    def test_breached_maps_to_suspended(self):
        assert LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.BREACHED] == "suspended"


class TestApplyTransitionRules:
    def test_no_change_when_same_state(self):
        state, reason, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.WARNING,
            raw_target=DrawdownGovernanceState.WARNING,
            utilization=0.60,
            cooldown_expires_at=None,
            evaluation_count=5,
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.WARNING
        assert reason == "no change"
        assert not blocked

    def test_escalation_overrides_cooldown(self):
        state, _, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.WARNING,
            raw_target=DrawdownGovernanceState.PROBATION,
            utilization=0.80,
            cooldown_expires_at=_NOW + timedelta(hours=3),
            evaluation_count=10,
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.PROBATION
        assert not blocked

    def test_first_escalation_deferred_below_min_observations(self):
        state, reason, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.NORMAL,
            raw_target=DrawdownGovernanceState.WARNING,
            utilization=0.60,
            cooldown_expires_at=None,
            evaluation_count=1,  # below min_observation_cycles=2
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.NORMAL
        assert blocked
        assert "deferred" in reason

    def test_first_escalation_allowed_at_min_observations(self):
        state, _, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.NORMAL,
            raw_target=DrawdownGovernanceState.WARNING,
            utilization=0.60,
            cooldown_expires_at=None,
            evaluation_count=2,  # exactly min_observation_cycles
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.WARNING
        assert not blocked

    def test_recovery_blocked_by_cooldown(self):
        state, reason, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.WARNING,
            raw_target=DrawdownGovernanceState.NORMAL,
            utilization=0.20,
            cooldown_expires_at=_NOW + timedelta(hours=3),
            evaluation_count=10,
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.WARNING
        assert blocked
        assert "cooldown" in reason

    def test_recovery_blocked_by_hysteresis(self):
        # 0.47 < warning threshold (0.50) but above recovery threshold (0.50 - 0.05 = 0.45)
        state, reason, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.WARNING,
            raw_target=DrawdownGovernanceState.NORMAL,
            utilization=0.47,
            cooldown_expires_at=None,
            evaluation_count=10,
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.WARNING
        assert blocked
        assert "hysteresis" in reason

    def test_recovery_allowed_below_hysteresis_band(self):
        # 0.44 < recovery threshold 0.45
        state, reason, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.WARNING,
            raw_target=DrawdownGovernanceState.NORMAL,
            utilization=0.44,
            cooldown_expires_at=None,
            evaluation_count=10,
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.NORMAL
        assert not blocked
        assert "recovery" in reason

    def test_recovery_one_step_at_a_time(self):
        # From PROBATION with raw_target NORMAL → can only recover to WARNING
        state, reason, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.PROBATION,
            raw_target=DrawdownGovernanceState.NORMAL,
            utilization=0.20,
            cooldown_expires_at=None,
            evaluation_count=10,
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.WARNING
        assert not blocked

    def test_breach_recovery_blocked_by_operator_ack_required(self):
        state, reason, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.BREACHED,
            raw_target=DrawdownGovernanceState.NORMAL,
            utilization=0.20,
            cooldown_expires_at=None,
            evaluation_count=10,
            operator_ack_required=True,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        assert state == DrawdownGovernanceState.BREACHED
        assert blocked
        assert "operator acknowledgement" in reason

    def test_breach_recovery_allowed_after_ack_cleared(self):
        state, _, blocked = apply_transition_rules(
            current_state=DrawdownGovernanceState.BREACHED,
            raw_target=DrawdownGovernanceState.NORMAL,
            utilization=0.20,
            cooldown_expires_at=None,
            evaluation_count=10,
            operator_ack_required=False,
            now=_NOW,
            config=_ENFORCE_CONFIG,
        )
        # One step: BREACHED → SUSPENDED
        assert state == DrawdownGovernanceState.SUSPENDED
        assert not blocked


# ===========================================================================
# Integration tests — DrawdownGovernanceService with in-memory DB
# ===========================================================================


class TestDrawdownGovernanceServiceDisabled:
    def test_disabled_config_returns_early(self, db_session: Session):
        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_DISABLED_CONFIG)
        assert not result.enabled
        assert result.strategies_evaluated == 0
        assert result.transitions == []


class TestDrawdownGovernanceServiceObserveMode:
    def test_observe_mode_does_not_persist_state(self, db_session: Session):
        # max_drawdown=0.12 (default); 0.09/0.12 = 75% → PROBATION
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.09)
        # Must have evaluation_count >= min_observation_cycles(2) so escalation isn't deferred
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_OBSERVE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        assert len(evals) == 1
        assert evals[0].drawdown_utilization == pytest.approx(0.75)
        assert evals[0].new_state == DrawdownGovernanceState.PROBATION

        # No state row updated in observe mode (read the seeded row — not mutated)
        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        # Row exists (seeded) but ladder_state still "normal" because observe mode doesn't write
        assert row is not None
        assert row.ladder_state == "normal"

    def test_observe_mode_still_emits_correct_scalars(self, db_session: Session):
        # 0.11/0.12 ≈ 91.7% → SUSPENDED
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.11)
        _seed_ladder_state(db_session, sid, ladder_state="warning", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_OBSERVE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        assert evals[0].new_state == DrawdownGovernanceState.SUSPENDED
        assert evals[0].allocation_scalar == pytest.approx(0.00)


class TestDrawdownGovernanceServiceEnforceMode:
    def test_normal_strategy_stays_normal(self, db_session: Session):
        # 0.03/0.12 = 25% → NORMAL
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.03)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        assert evals[0].new_state == DrawdownGovernanceState.NORMAL
        assert evals[0].allocation_scalar == pytest.approx(1.00)
        assert not evals[0].did_transition

    def test_warning_escalation_persists_state(self, db_session: Session):
        # 0.075/0.12 = 62.5% → WARNING
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.075)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.WARNING
        assert ev.allocation_scalar == pytest.approx(0.50)
        assert ev.did_transition

        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        assert row is not None
        assert row.ladder_state == "warning"
        assert row.allocation_scalar == pytest.approx(0.50)

    def test_probation_escalation(self, db_session: Session):
        # 0.096/0.12 = 80% → PROBATION
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.096)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.PROBATION
        assert ev.allocation_scalar == pytest.approx(0.25)

    def test_suspension_escalation(self, db_session: Session):
        # 0.108/0.12 = 90% → SUSPENDED
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.108)
        _seed_ladder_state(db_session, sid, ladder_state="probation", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.SUSPENDED
        assert ev.allocation_scalar == pytest.approx(0.00)

    def test_breach_escalation_sets_operator_ack(self, db_session: Session):
        # 0.13/0.12 ≈ 108% → BREACHED
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.13)
        _seed_ladder_state(db_session, sid, ladder_state="suspended", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.BREACHED
        assert ev.allocation_scalar == pytest.approx(0.00)
        assert ev.operator_ack_required

        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        assert row is not None
        assert row.operator_ack_required

    def test_transition_audit_trail_written(self, db_session: Session):
        # 0.075/0.12 = 62.5% → WARNING
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.075)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        svc.run(config=_ENFORCE_CONFIG)

        repo = DrawdownGovernanceLadderTransitionRepository(db_session)
        transitions = repo.get_recent_for_strategy(sid)
        assert len(transitions) == 1
        t = transitions[0]
        assert t.from_state == "normal"
        assert t.to_state == "warning"

    def test_skips_strategy_without_live_snapshot(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)  # No live snapshot

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        if evals:
            assert evals[0].skipped
            assert evals[0].skip_reason == "missing_drawdown_data"

    def test_non_monitorable_strategy_excluded(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid, state="proposed")
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.13)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        assert len(evals) == 0

    def test_run_counters_accurate(self, db_session: Session):
        sids = [f"strat_{uuid4().hex[:8]}" for _ in range(3)]
        # warning: 0.075/0.12 = 62.5%
        _seed_strategy(db_session, sids[0])
        _seed_live_snapshot(db_session, sids[0], realized_drawdown=0.075)
        _seed_ladder_state(db_session, sids[0], ladder_state="normal", evaluation_count=5)
        # breach: 0.13/0.12 = 108%
        _seed_strategy(db_session, sids[1])
        _seed_live_snapshot(db_session, sids[1], realized_drawdown=0.13)
        _seed_ladder_state(db_session, sids[1], ladder_state="suspended", evaluation_count=5)
        # normal: 0.03/0.12 = 25%
        _seed_strategy(db_session, sids[2])
        _seed_live_snapshot(db_session, sids[2], realized_drawdown=0.03)
        _seed_ladder_state(db_session, sids[2], ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        assert result.warnings_triggered == 1
        assert result.breaches_triggered == 1
        assert result.probations_triggered == 0


class TestDrawdownGovernanceServiceAntiFlapping:
    def test_escalation_deferred_below_min_observation_cycles(self, db_session: Session):
        # 0.075/0.12 = 62.5% → would be WARNING, but blocked by min_observation_cycles
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.075)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=1)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        assert evals[0].new_state == DrawdownGovernanceState.NORMAL
        assert evals[0].transition_blocked_by_cooldown

    def test_recovery_blocked_by_cooldown(self, db_session: Session):
        # 0.03/0.12 = 25% → would recover, but cooldown blocks it
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.03)
        future_cooldown = datetime.now(UTC) + timedelta(hours=3)
        _seed_ladder_state(
            db_session,
            sid,
            ladder_state="warning",
            evaluation_count=10,
            cooldown_expires_at=future_cooldown,
        )

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.WARNING
        assert ev.transition_blocked_by_cooldown

    def test_recovery_blocked_by_hysteresis_band(self, db_session: Session):
        # 0.054/0.12 = 45%: below warning threshold (50%) but above
        # recovery threshold (50% - 5% = 45%); exactly at boundary → still blocked
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.054)
        _seed_ladder_state(db_session, sid, ladder_state="warning", evaluation_count=10)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.WARNING
        assert ev.transition_blocked_by_cooldown

    def test_recovery_one_step_at_a_time(self, db_session: Session):
        # 0.01/0.12 ≈ 8% → raw target NORMAL, but from PROBATION only one step allowed
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.01)
        _seed_ladder_state(db_session, sid, ladder_state="probation", evaluation_count=10)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.WARNING  # one step, not NORMAL
        assert ev.did_transition

    def test_cooldown_window_set_after_transition(self, db_session: Session):
        # Escalates NORMAL → WARNING; cooldown should be set for ~6 hours
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.075)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        svc.run(config=_ENFORCE_CONFIG)

        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        assert row is not None
        assert row.cooldown_expires_at is not None
        expected_min = datetime.now(UTC) + timedelta(hours=5)
        assert row.cooldown_expires_at > expected_min


class TestDrawdownGovernanceServiceRecovery:
    def test_full_recovery_warning_to_normal(self, db_session: Session):
        # 0.03/0.12 = 25% → well below recovery threshold (50%-5%=45%)
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.03)
        _seed_ladder_state(db_session, sid, ladder_state="warning", evaluation_count=10)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        evals = [e for e in result.results if e.strategy_id == sid]
        ev = evals[0]
        assert ev.new_state == DrawdownGovernanceState.NORMAL
        assert ev.did_transition
        assert ev.allocation_scalar == pytest.approx(1.00)
        assert result.recoveries_triggered >= 1

    def test_recovery_counter_increments(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.01)
        _seed_ladder_state(db_session, sid, ladder_state="warning", evaluation_count=10)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_ENFORCE_CONFIG)

        assert result.recoveries_triggered >= 1


class TestDrawdownGovernanceServiceAcknowledgeBreach:
    def test_acknowledge_clears_ack_flag(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_ladder_state(db_session, sid, ladder_state="breached", operator_ack_required=True)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.acknowledge_breach(sid, operator="ops_user", reason="reviewed and approved")

        assert result is True

        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        assert row is not None
        assert not row.operator_ack_required
        assert row.operator_acknowledged_by == "ops_user"

    def test_acknowledge_returns_false_when_no_ack_needed(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_ladder_state(db_session, sid, ladder_state="warning", operator_ack_required=False)

        svc = DrawdownGovernanceService(session=db_session)
        result = svc.acknowledge_breach(sid, operator="ops_user", reason="test")

        assert result is False

    def test_acknowledge_returns_false_when_no_state(self, db_session: Session):
        svc = DrawdownGovernanceService(session=db_session)
        result = svc.acknowledge_breach("nonexistent_strategy", operator="ops", reason="test")
        assert result is False

    def test_acknowledge_writes_transition_row(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_ladder_state(db_session, sid, ladder_state="breached", operator_ack_required=True)

        svc = DrawdownGovernanceService(session=db_session)
        svc.acknowledge_breach(sid, operator="ops_user", reason="reviewed")

        repo = DrawdownGovernanceLadderTransitionRepository(db_session)
        transitions = repo.get_recent_for_strategy(sid)
        assert any("operator_ack" in (t.transition_reason or "") for t in transitions)


class TestDrawdownGovernancePersistence:
    def test_evaluation_count_increments_across_runs(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.02)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        svc.run(config=_ENFORCE_CONFIG)
        svc.run(config=_ENFORCE_CONFIG)

        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        assert row is not None
        assert row.evaluation_count >= 7  # 5 seeded + 2 runs

    def test_new_strategy_creates_state_row(self, db_session: Session):
        # 0.075/0.12 = 62.5% → WARNING (use min_observation_cycles=0 to skip deferral)
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.075)

        no_defer_config = DrawdownGovernanceLadderConfig(
            enabled=True, mode="enforce", min_observation_cycles=0
        )
        svc = DrawdownGovernanceService(session=db_session)
        svc.run(config=no_defer_config)

        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        assert row is not None
        assert row.ladder_state == "warning"

    def test_last_evaluated_at_updated(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.02)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        svc.run(config=_ENFORCE_CONFIG)

        repo = DrawdownGovernanceLadderStateRepository(db_session)
        row = repo.get_for_strategy(sid)
        assert row is not None
        assert row.last_evaluated_at is not None

    def test_transition_row_recorded_on_state_change(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.075)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        svc.run(config=_ENFORCE_CONFIG)

        repo = DrawdownGovernanceLadderTransitionRepository(db_session)
        transitions = repo.get_recent_for_strategy(sid)
        assert len(transitions) == 1
        assert transitions[0].drawdown_utilization is not None
        assert transitions[0].allocation_scalar_after is not None

    def test_no_transition_row_when_state_unchanged(self, db_session: Session):
        sid = f"strat_{uuid4().hex[:8]}"
        _seed_strategy(db_session, sid)
        _seed_live_snapshot(db_session, sid, realized_drawdown=0.02)
        _seed_ladder_state(db_session, sid, ladder_state="normal", evaluation_count=5)

        svc = DrawdownGovernanceService(session=db_session)
        svc.run(config=_ENFORCE_CONFIG)

        repo = DrawdownGovernanceLadderTransitionRepository(db_session)
        transitions = repo.get_recent_for_strategy(sid)
        assert len(transitions) == 0


class TestDrawdownGovernanceServiceResultJsonable:
    def test_result_to_jsonable_serialises_correctly(self, db_session: Session):
        svc = DrawdownGovernanceService(session=db_session)
        result = svc.run(config=_DISABLED_CONFIG)
        payload = DrawdownGovernanceService.result_to_jsonable(result)
        assert "run_id" in payload
        assert "enabled" in payload
        assert payload["enabled"] is False
        assert "strategies_evaluated" in payload


class TestDrawdownGovernanceLadderConfigHelpers:
    def test_allocation_scalar_for_each_state(self):
        cfg = _ENFORCE_CONFIG
        assert cfg.allocation_scalar_for(DrawdownGovernanceState.NORMAL) == 1.00
        assert cfg.allocation_scalar_for(DrawdownGovernanceState.WARNING) == 0.50
        assert cfg.allocation_scalar_for(DrawdownGovernanceState.PROBATION) == 0.25
        assert cfg.allocation_scalar_for(DrawdownGovernanceState.SUSPENDED) == 0.00
        assert cfg.allocation_scalar_for(DrawdownGovernanceState.BREACHED) == 0.00

    def test_cooldown_hours_for_each_state(self):
        cfg = _ENFORCE_CONFIG
        assert cfg.cooldown_hours_for(DrawdownGovernanceState.WARNING) == 6.0
        assert cfg.cooldown_hours_for(DrawdownGovernanceState.PROBATION) == 12.0
        assert cfg.cooldown_hours_for(DrawdownGovernanceState.SUSPENDED) == 24.0
        assert cfg.cooldown_hours_for(DrawdownGovernanceState.BREACHED) == 48.0
        assert cfg.cooldown_hours_for(DrawdownGovernanceState.NORMAL) == 0.0
