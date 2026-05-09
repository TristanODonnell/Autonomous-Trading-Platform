from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.queries.active_strategies_repository import (
    ActiveStrategiesRepository,
)


def test_lists_active_strategies_with_config_fill_and_allocation_data(
    db_session: Session,
) -> None:
    now = datetime(2026, 5, 8, 15, 30, tzinfo=UTC)
    run_id = uuid4()
    intent_id = uuid4()

    db_session.add_all(
        [
            StrategyGovernance(
                strategy_id="momentum_v1",
                config_hash="momentum-hash",
                current_state="approved_for_paper_trading",
                experiment_id="experiment-1",
                source_run_id=None,
                submitted_at=now,
                updated_at=now,
                submitted_by="test",
            ),
            StrategyGovernance(
                strategy_id="live_v1",
                config_hash="live-hash",
                current_state="approved_for_live_trading",
                experiment_id="experiment-1",
                source_run_id=None,
                submitted_at=now,
                updated_at=now,
                submitted_by="test",
            ),
            StrategyGovernance(
                strategy_id="draft_v1",
                config_hash="draft-hash",
                current_state="draft",
                experiment_id="experiment-1",
                source_run_id=None,
                submitted_at=now,
                updated_at=now,
                submitted_by="test",
            ),
            StrategyConfigs(
                strategy_id="momentum_v1",
                config_hash="momentum-hash",
                config_json={},
                created_at=now,
                strategy_type="momentum",
                metadata_json={
                    "display_name": "Momentum V1",
                    "performance_tier": "tier_1",
                    "todays_return": "0.0125",
                },
            ),
            StrategyConfigs(
                strategy_id="live_v1",
                config_hash="live-hash",
                config_json={"display_name": "Live Strategy"},
                created_at=now,
                strategy_type="mean_reversion",
                metadata_json=None,
            ),
            CapitalAllocationPolicies(
                policy_id="paper-tier-1",
                approval_status="approved_paper",
                performance_tier="tier_1",
                max_pct_of_capital=0.05,
                max_position_size_usd=10000.0,
                max_drawdown_allowed=0.1,
                is_active=True,
                created_at=now,
                notes=None,
            ),
            CapitalAllocationPolicies(
                policy_id="live-default",
                approval_status="approved_live",
                performance_tier=None,
                max_pct_of_capital=0.10,
                max_position_size_usd=50000.0,
                max_drawdown_allowed=0.1,
                is_active=True,
                created_at=now,
                notes=None,
            ),
            AllocationOverrides(
                override_id="momentum-override",
                strategy_id="momentum_v1",
                overridden_by="operator",
                override_reason="dashboard test",
                max_pct_of_capital=None,
                max_position_size_usd=25000.0,
                max_drawdown_allowed=None,
                is_active=True,
                created_at=now,
                expires_at=now + timedelta(days=1),
            ),
            OrderIntents(
                intent_id=intent_id,
                idempotency_key="idem-1",
                run_id=run_id,
                strategy_id="momentum_v1",
                timestamp=now,
                bar_timestamp=now,
                symbol="AAPL",
                side=Side.BUY,
                qty=Decimal("1"),
                notional=None,
                order_type=OrderType.MARKET,
                limit_price=None,
                stop_price=None,
                time_in_force=TimeInForce.DAY,
                extended_hours=False,
                client_order_id="client-1",
                meta=None,
            ),
            Fill(
                fill_id="fill-1",
                broker_order_id="broker-1",
                intent_id=intent_id,
                run_id=run_id,
                timestamp=now,
                symbol="AAPL",
                side=Side.BUY,
                quantity=Decimal("1"),
                price=Decimal("190.00"),
                fees=None,
                liquidity=None,
                venue=None,
                meta=None,
            ),
        ]
    )
    db_session.flush()

    repository = ActiveStrategiesRepository(db_session)

    result = repository.list_active_strategies(now=now)

    assert [row.strategy_id for row in result] == ["live_v1", "momentum_v1"]

    live = result[0]
    assert live.display_name == "Live Strategy"
    assert live.strategy_type == "mean_reversion"
    assert live.status == "live"
    assert live.trade_count_today == 0
    assert live.allocated_capital == Decimal("50000.0")
    assert live.enabled is True

    momentum = result[1]
    assert momentum.display_name == "Momentum V1"
    assert momentum.strategy_type == "momentum"
    assert momentum.status == "paper"
    assert momentum.todays_return == Decimal("0.0125")
    assert momentum.trade_count_today == 1
    assert momentum.allocated_capital == Decimal("25000.0")
    assert momentum.enabled is True


def test_active_strategy_enabled_reflects_strategy_control_state(
    db_session: Session,
) -> None:
    now = datetime(2026, 5, 8, 15, 30, tzinfo=UTC)
    db_session.add_all(
        [
            StrategyGovernance(
                strategy_id="momentum_v1",
                config_hash="momentum-hash",
                current_state="approved_for_paper_trading",
                experiment_id="experiment-1",
                source_run_id=None,
                submitted_at=now,
                updated_at=now,
                submitted_by="test",
            ),
            StrategyConfigs(
                strategy_id="momentum_v1",
                config_hash="momentum-hash",
                config_json={},
                created_at=now,
                strategy_type="momentum",
                metadata_json={"display_name": "Momentum V1"},
            ),
            StrategyControlState(
                strategy_id="momentum_v1",
                enabled=False,
                reason="operator disabled",
                updated_by="operator-1",
                updated_at=now,
            ),
        ]
    )
    db_session.flush()

    repository = ActiveStrategiesRepository(db_session)

    result = repository.list_active_strategies(now=now)

    assert len(result) == 1
    assert result[0].strategy_id == "momentum_v1"
    assert result[0].status == "paper"
    assert result[0].enabled is False
