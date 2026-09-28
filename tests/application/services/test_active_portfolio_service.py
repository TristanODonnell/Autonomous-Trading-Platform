from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_health_state import (
    StrategyHealthStateRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    StrategySleevePositionRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)
from tests.application.services.test_quality_based_reallocation_service import (
    _seed_policy,
    _seed_strategy,
)

_T0 = datetime(2026, 9, 1, 21, 0, tzinfo=UTC)


def _eligible(
    session: Session,
    strategy_id: str,
    *,
    state: str = "approved_for_paper_trading",
    with_config: bool = True,
) -> None:
    session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            current_state=state,
            experiment_id="test",
            source_run_id=None,
            submitted_at=_T0,
            updated_at=_T0,
            submitted_by="test",
        )
    )
    if with_config:
        session.add(
            StrategyConfigs(
                strategy_id=strategy_id,
                config_hash=f"{strategy_id}_hash",
                config_json={},
                created_at=_T0,
                strategy_type="test",
            )
        )
    session.flush()


def _set_state(session: Session, strategy_id: str, state: str) -> None:
    row = session.query(StrategyGovernance).filter_by(strategy_id=strategy_id).one()
    row.current_state = state
    session.flush()


def _hold(session: Session, strategy_id: str, symbol: str = "AAPL") -> None:
    session.add(
        StrategySleevePositionRow(
            strategy_id=strategy_id,
            symbol=symbol,
            quantity=Decimal("10"),
            avg_cost=Decimal("100"),
            updated_at=_T0,
        )
    )
    session.flush()


def _settings(session: Session, **values: object) -> None:
    OperatorSettingsRepository(session).update_current(values, updated_by="test")


def _service(
    session: Session,
    scores: dict[str, float],
    environment: TradingEnvironment = TradingEnvironment.PAPER,
) -> ActivePortfolioService:
    return ActivePortfolioService(
        session,
        trading_environment=environment,
        quality_score_fn=lambda sid: Decimal(str(scores[sid])),
    )


def _statuses(session: Session) -> dict[str, str]:
    return {row.strategy_id: row.status for row in PortfolioMembershipRepository(session).get_all()}


class TestSelection:
    def test_fills_up_to_max_by_quality(self, db_session: Session) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=2)
        scores = {"a": 1.0, "b": 3.0, "c": 2.0}
        for sid in scores:
            _eligible(db_session, sid)

        result = _service(db_session, scores).refresh(now=_T0)

        assert result.active == ["b", "c"]
        assert result.added == ["b", "c"]
        assert not result.below_minimum
        assert _statuses(db_session) == {"b": "active", "c": "active"}

    def test_reports_below_minimum_without_inventing_members(self, db_session: Session) -> None:
        _settings(db_session, min_active_strategies=3, max_active_strategies=6)
        _eligible(db_session, "only")

        result = _service(db_session, {"only": 1.0}).refresh(now=_T0)

        assert result.active == ["only"]
        assert result.below_minimum

    def test_incumbents_keep_their_seat_when_a_better_challenger_appears(
        self, db_session: Session
    ) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=2)
        scores = {"a": 1.0, "b": 2.0}
        for sid in scores:
            _eligible(db_session, sid)
        service = _service(db_session, scores)
        service.refresh(now=_T0)

        _eligible(db_session, "star")
        scores["star"] = 99.0
        result = service.refresh(now=_T0 + timedelta(days=1))

        assert result.active == ["a", "b"]
        assert result.added == [] and result.removed == []

    def test_open_seat_goes_to_best_challenger(self, db_session: Session) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=2)
        scores = {"a": 1.0, "b": 2.0, "c": 0.5, "d": 5.0}
        for sid in ("a", "b"):
            _eligible(db_session, sid)
        service = _service(db_session, scores)
        service.refresh(now=_T0)

        for sid in ("c", "d"):
            _eligible(db_session, sid)
        _set_state(db_session, "a", "retired")
        result = service.refresh(now=_T0 + timedelta(days=1))

        assert result.active == ["b", "d"]
        assert result.removed == ["a"]

    def test_lowering_max_drops_lowest_scored_incumbents(self, db_session: Session) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=3)
        scores = {"a": 1.0, "b": 2.0, "c": 3.0}
        for sid in scores:
            _eligible(db_session, sid)
        service = _service(db_session, scores)
        service.refresh(now=_T0)

        _settings(db_session, max_active_strategies=2)
        result = service.refresh(now=_T0 + timedelta(days=1))

        assert result.active == ["b", "c"]
        reasons = {t.strategy_id: t.reason for t in result.transitions}
        assert reasons == {"a": "over_max_active"}


class TestWindDown:
    def test_removed_member_holding_positions_winds_down(self, db_session: Session) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=3)
        scores = {"a": 1.0, "b": 2.0}
        for sid in scores:
            _eligible(db_session, sid)
        service = _service(db_session, scores)
        service.refresh(now=_T0)
        _hold(db_session, "a")

        _set_state(db_session, "a", "candidate")
        result = service.refresh(now=_T0 + timedelta(days=1))

        assert result.winding_down == ["a"]
        assert _statuses(db_session)["a"] == MembershipStatus.WINDING_DOWN

    def test_removed_member_with_flat_sleeve_goes_inactive(self, db_session: Session) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=3)
        _eligible(db_session, "a")
        service = _service(db_session, {"a": 1.0})
        service.refresh(now=_T0)

        _set_state(db_session, "a", "retired")
        result = service.refresh(now=_T0 + timedelta(days=1))

        assert result.winding_down == []
        assert _statuses(db_session)["a"] == MembershipStatus.INACTIVE

    def test_wind_down_completes_once_the_sleeve_is_flat(self, db_session: Session) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=3)
        _eligible(db_session, "a")
        service = _service(db_session, {"a": 1.0})
        service.refresh(now=_T0)
        _hold(db_session, "a")
        _set_state(db_session, "a", "retired")
        service.refresh(now=_T0 + timedelta(days=1))

        db_session.query(StrategySleevePositionRow).filter_by(strategy_id="a").delete()
        result = service.refresh(now=_T0 + timedelta(days=2))

        assert result.wind_down_completed == ["a"]
        assert _statuses(db_session)["a"] == MembershipStatus.INACTIVE
        history = PortfolioMembershipRepository(db_session).get_transitions("a")
        assert [(t.from_status, t.to_status) for t in history] == [
            (None, "active"),
            ("active", "winding_down"),
            ("winding_down", "inactive"),
        ]

    def test_winding_down_member_is_reactivated_when_eligible_again(
        self, db_session: Session
    ) -> None:
        _settings(db_session, min_active_strategies=1, max_active_strategies=3)
        _eligible(db_session, "a")
        service = _service(db_session, {"a": 1.0})
        service.refresh(now=_T0)
        _hold(db_session, "a")
        _set_state(db_session, "a", "candidate")
        service.refresh(now=_T0 + timedelta(days=1))

        _set_state(db_session, "a", "approved_for_paper_trading")
        result = service.refresh(now=_T0 + timedelta(days=2))

        assert result.active == ["a"]
        assert [t.reason for t in result.transitions] == ["reactivated"]


class TestEligibility:
    def test_filters_state_config_control_and_suspension(self, db_session: Session) -> None:
        _eligible(db_session, "ok")
        _eligible(db_session, "live_ok", state="approved_for_live_trading")
        _eligible(db_session, "bench", state="candidate")
        _eligible(db_session, "no_config", with_config=False)
        _eligible(db_session, "disabled")
        _eligible(db_session, "suspended")
        db_session.add(
            StrategyControlState(
                strategy_id="disabled", enabled=False, reason="off", updated_at=_T0
            )
        )
        db_session.add(
            StrategyHealthStateRow(
                health_id="h1",
                strategy_id="suspended",
                health_status="suspended",
                created_at=_T0,
                updated_at=_T0,
            )
        )
        db_session.flush()

        assert _service(db_session, {}).eligible_strategy_ids() == ["live_ok", "ok"]

    def test_live_environment_only_uses_live_approved(self, db_session: Session) -> None:
        _eligible(db_session, "paper_only")
        _eligible(db_session, "live_ok", state="approved_for_live_trading")

        service = _service(db_session, {}, environment=TradingEnvironment.LIVE)

        assert service.eligible_strategy_ids() == ["live_ok"]


class TestBudgets:
    def _activate(self, session: Session, ids: list[str], *, max_active: int = 6) -> None:
        _settings(session, min_active_strategies=1, max_active_strategies=max_active)
        for sid in ids:
            _eligible(session, sid)
        _service(session, dict.fromkeys(ids, 1.0)).refresh(now=_T0)

    def _override(self, session: Session, strategy_id: str, pct: float) -> None:
        session.add(
            AllocationOverrides(
                override_id=f"ov_{strategy_id}",
                strategy_id=strategy_id,
                overridden_by="auto_rebalance",
                override_reason="test",
                max_pct_of_capital=pct,
                is_active=True,
                created_at=_T0,
            )
        )
        session.flush()

    def _budgets(self, session: Session) -> dict[str, Decimal]:
        service = _service(session, {})
        return {b.strategy_id: b.pct_of_capital for b in service.budgets(now=_T0)}

    def test_equal_weight_within_deployable_fraction(self, db_session: Session) -> None:
        self._activate(db_session, ["a", "b", "c", "d"])
        _settings(db_session, max_total_strategy_allocation_pct=0.95, per_strategy_cap=0.5)

        budgets = self._budgets(db_session)

        assert budgets == {sid: Decimal("0.2375") for sid in "abcd"}

    def test_overrides_are_used_and_scaled_to_fit(self, db_session: Session) -> None:
        self._activate(db_session, ["a", "b"])
        _settings(db_session, max_total_strategy_allocation_pct=0.9, per_strategy_cap=1.0)
        self._override(db_session, "a", 0.6)
        self._override(db_session, "b", 0.6)

        budgets = self._budgets(db_session)

        assert budgets == {"a": Decimal("0.45"), "b": Decimal("0.45")}

    def test_per_strategy_cap_clamps_and_leaves_cash(self, db_session: Session) -> None:
        self._activate(db_session, ["a", "b"])
        _settings(db_session, max_total_strategy_allocation_pct=1.0, per_strategy_cap=0.25)

        budgets = self._budgets(db_session)

        assert budgets == {"a": Decimal("0.25"), "b": Decimal("0.25")}

    def test_winding_down_member_gets_zero(self, db_session: Session) -> None:
        self._activate(db_session, ["a", "b"])
        _settings(db_session, max_total_strategy_allocation_pct=1.0, per_strategy_cap=1.0)
        _hold(db_session, "a")
        _set_state(db_session, "a", "candidate")
        _service(db_session, {"b": 1.0}).refresh(now=_T0 + timedelta(days=1))

        budgets = self._budgets(db_session)

        assert budgets == {"a": Decimal("0"), "b": Decimal("1")}

    @pytest.mark.parametrize("n", [3, 6, 7])
    def test_budgets_never_exceed_deployable(self, db_session: Session, n: int) -> None:
        ids = [f"s{i}" for i in range(n)]
        self._activate(db_session, ids, max_active=6)
        _settings(db_session, max_total_strategy_allocation_pct=0.95, per_strategy_cap=1.0)
        for sid in ids[:2]:
            self._override(db_session, sid, 0.7)

        total = sum(self._budgets(db_session).values(), Decimal("0"))

        assert total <= Decimal("0.95")


class TestQualityIntegration:
    def test_default_scorer_prefers_the_stronger_backtest(self, db_session: Session) -> None:
        _seed_policy(db_session, max_pct=0.8)
        _seed_strategy(db_session, "good", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
        _seed_strategy(db_session, "bad", sharpe=-0.5, total_return=-0.10, max_drawdown=0.30)
        _settings(db_session, min_active_strategies=1, max_active_strategies=1)

        result = ActivePortfolioService(db_session).refresh(now=_T0)

        assert result.active == ["good"]

    def test_reallocation_only_splits_capital_among_active_members(
        self, db_session: Session
    ) -> None:
        _seed_policy(db_session, max_pct=0.8)
        for sid, sharpe in (("good", 2.0), ("ok", 1.0), ("benched", 1.5)):
            _seed_strategy(db_session, sid, sharpe=sharpe, total_return=0.1, max_drawdown=0.05)
        _settings(
            db_session,
            min_active_strategies=1,
            max_active_strategies=2,
            auto_rebalance_enabled=True,
            per_strategy_cap=1.0,
        )
        ActivePortfolioService(
            db_session,
            quality_score_fn=lambda sid: Decimal({"good": 3, "ok": 2, "benched": 1}[sid]),
        ).refresh(now=_T0)

        result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

        assert set(result.after_allocation) == {"good", "ok"}
