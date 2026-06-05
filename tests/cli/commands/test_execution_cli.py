from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import execution
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import (
    FillQualityMetrics,
)
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.tracked_orders import TrackedOrder


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        allow_live=False,
        paper_only=False,
        output=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _json_from_output(output: str) -> dict[Any, Any]:
    start = output.find("{")
    assert start >= 0
    return cast(dict[Any, Any], json.loads(output[start:]))


class TestParserRegistration:
    def test_reconcile_open_orders_has_run_id_and_dry_run(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "execution",
                "reconcile-open-orders",
                "--run-id",
                "00000000-0000-0000-0000-000000000301",
                "--dry-run",
            ]
        )
        assert args.func is execution.handle_reconcile_open_orders
        assert args.run_id == "00000000-0000-0000-0000-000000000301"
        assert args.dry_run is True

    @pytest.mark.parametrize(
        ("command", "handler"),
        [
            (["broker-health"], execution.handle_broker_health),
            (
                ["sync-broker-state", "--run-id", "00000000-0000-0000-0000-000000000301"],
                execution.handle_sync_broker_state,
            ),
            (
                ["validate-broker-consistency", "--tolerance", "0.05"],
                execution.handle_validate_broker_consistency,
            ),
            (
                [
                    "sync-position-snapshot",
                    "--run-id",
                    "00000000-0000-0000-0000-000000000301",
                ],
                execution.handle_sync_position_snapshot,
            ),
            (
                [
                    "sync-open-orders",
                    "--run-id",
                    "00000000-0000-0000-0000-000000000301",
                    "--account-id",
                    "paper-account-1",
                ],
                execution.handle_sync_open_orders,
            ),
            (
                [
                    "sync-order-status",
                    "--order-id",
                    "broker-order-1",
                    "--run-id",
                    "00000000-0000-0000-0000-000000000301",
                    "--account-id",
                    "paper-account-1",
                ],
                execution.handle_sync_order_status,
            ),
            (
                [
                    "reconcile-order-fills",
                    "--order-id",
                    "broker-order-1",
                    "--run-id",
                    "00000000-0000-0000-0000-000000000301",
                    "--account-id",
                    "paper-account-1",
                ],
                execution.handle_reconcile_order_fills,
            ),
            (
                [
                    "external-reconcile",
                    "--run-id",
                    "00000000-0000-0000-0000-000000000301",
                ],
                execution.handle_external_reconcile,
            ),
            (
                [
                    "inspect-fill-quality",
                    "--intent-id",
                    "00000000-0000-0000-0000-000000000201",
                ],
                execution.handle_inspect_fill_quality,
            ),
            (
                [
                    "list-fill-quality",
                    "--run-id",
                    "00000000-0000-0000-0000-000000000301",
                    "--adverse-only",
                    "--limit",
                    "25",
                ],
                execution.handle_list_fill_quality,
            ),
            (
                ["inspect-runtime-state", "--strategy-id", "baseline_strategy"],
                execution.handle_inspect_runtime_state,
            ),
            (["list-open-orders", "--source", "both"], execution.handle_list_open_orders),
            (
                ["cancel-order", "--broker-order-id", "broker-order-1", "--dry-run"],
                execution.handle_cancel_order,
            ),
            (
                ["stream-orders", "--duration-seconds", "1", "--dry-run"],
                execution.handle_stream_orders,
            ),
            (
                [
                    "submit-intents",
                    "--run-id",
                    "00000000-0000-0000-0000-000000000301",
                ],
                execution.handle_submit_intents,
            ),
            (
                [
                    "policy-preview",
                    "--intent-id",
                    "00000000-0000-0000-0000-000000000201",
                    "--policy-mode",
                    "twap",
                    "--offline",
                    "--mid-price",
                    "185.25",
                ],
                execution.handle_policy_preview,
            ),
        ],
    )
    def test_missing_coverage_commands_registered(self, command: list[str], handler) -> None:
        parser = build_parser()
        args = parser.parse_args(["execution", *command])
        assert args.func is handler


class TestBrokerSafety:
    def test_broker_health_refuses_live_without_allow_live(self, monkeypatch) -> None:
        monkeypatch.setattr(
            execution,
            "Settings",
            lambda: SimpleNamespace(trading_environment=TradingEnvironment.LIVE),
        )
        monkeypatch.setattr(
            execution,
            "_build_broker_client",
            lambda _settings: pytest.fail("broker client should not be constructed"),
        )

        with pytest.raises(RuntimeError, match="Refusing broker CLI command in live mode"):
            execution.handle_broker_health(_args())

    def test_broker_health_uses_health_check_result(self, monkeypatch, capsys) -> None:
        class FakeBrokerClient:
            def get_account(self) -> dict:
                return {"id": "paper-account-1"}

        monkeypatch.setattr(
            execution,
            "Settings",
            lambda: SimpleNamespace(trading_environment=TradingEnvironment.PAPER),
        )
        monkeypatch.setattr(execution, "_build_broker_client", lambda _settings: FakeBrokerClient())

        assert execution.handle_broker_health(_args()) == 0
        payload = _json_from_output(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["account_id"] == "paper-account-1"


class TestBrokerOrderIdValidation:
    def test_sync_order_status_rejects_placeholder_before_settings(
        self,
        db_session: Session,
        monkeypatch,
        capsys,
    ) -> None:
        monkeypatch.setattr(execution, "get_session", lambda: db_session)
        monkeypatch.setattr(
            execution,
            "Settings",
            lambda: pytest.fail("invalid broker order id should not construct settings"),
        )

        result = execution.handle_sync_order_status(
            _args(
                order_id="broker-order-id",
                run_id="00000000-0000-0000-0000-000000000301",
                account_id="paper-account-1",
            )
        )

        assert result == 2
        payload = _json_from_output(capsys.readouterr().out)
        assert payload["status"] == "invalid_argument"
        assert payload["field"] == "order_id"
        assert "real Alpaca order UUID" in payload["message"]

    def test_reconcile_order_fills_rejects_placeholder_before_settings(
        self,
        db_session: Session,
        monkeypatch,
        capsys,
    ) -> None:
        monkeypatch.setattr(execution, "get_session", lambda: db_session)
        monkeypatch.setattr(
            execution,
            "Settings",
            lambda: pytest.fail("invalid broker order id should not construct settings"),
        )

        result = execution.handle_reconcile_order_fills(
            _args(
                order_id="broker-order-id",
                run_id="00000000-0000-0000-0000-000000000301",
                account_id="paper-account-1",
            )
        )

        assert result == 2
        payload = _json_from_output(capsys.readouterr().out)
        assert payload["status"] == "invalid_argument"
        assert payload["field"] == "order_id"

    def test_cancel_order_rejects_placeholder_when_confirmed(
        self,
        db_session: Session,
        monkeypatch,
        capsys,
    ) -> None:
        monkeypatch.setattr(execution, "get_session", lambda: db_session)
        monkeypatch.setattr(
            execution,
            "Settings",
            lambda: pytest.fail("invalid broker order id should not construct settings"),
        )

        result = execution.handle_cancel_order(
            _args(
                broker_order_id="broker-order-id",
                dry_run=False,
                confirm_cancel=True,
            )
        )

        assert result == 2
        payload = _json_from_output(capsys.readouterr().out)
        assert payload["status"] == "invalid_argument"
        assert payload["field"] == "broker_order_id"


class TestReconcileOpenOrders:
    def test_dry_run_lists_open_orders_without_scheduler(
        self,
        db_session: Session,
        monkeypatch,
        capsys,
    ) -> None:
        run_id = UUID("00000000-0000-0000-0000-000000000301")
        order = TrackedOrder(
            order_id=uuid4(),
            intent_id=uuid4(),
            run_id=run_id,
            strategy_id="baseline_strategy",
            account_id="paper-account-1",
            symbol="AAPL",
            broker_order_id="broker-order-1",
            current_status=OrderStatus.SUBMITTED,
            previous_filled_qty=Decimal("0"),
            previous_avg_fill_price=None,
            is_open=True,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        db_session.add(order)
        db_session.flush()

        monkeypatch.setattr(
            execution,
            "Settings",
            lambda: SimpleNamespace(trading_environment=TradingEnvironment.PAPER),
        )
        monkeypatch.setattr(execution, "get_session", lambda: db_session)
        monkeypatch.setattr(
            execution,
            "run_order_reconciliation_job",
            lambda **_kwargs: pytest.fail("scheduler job should not run during dry-run"),
        )

        result = execution.handle_reconcile_open_orders(_args(run_id=str(run_id), dry_run=True))

        assert result == 0
        payload = _json_from_output(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["open_order_count"] == 1
        assert payload["orders"][0]["broker_order_id"] == "broker-order-1"


class TestFillQualityInspection:
    def test_inspect_fill_quality_by_intent(
        self,
        db_session: Session,
        monkeypatch,
        capsys,
    ) -> None:
        run_id = "00000000-0000-0000-0000-000000000301"
        intent_id = "00000000-0000-0000-0000-000000000201"
        row = FillQualityMetrics(
            record_id="00000000-0000-0000-0000-000000000401",
            run_id=run_id,
            intent_id=intent_id,
            fill_id="fill-1",
            strategy_id="baseline_strategy",
            symbol="AAPL",
            signal_timestamp=datetime(2026, 1, 1, 12, tzinfo=UTC),
            submission_timestamp=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
            fill_timestamp=datetime(2026, 1, 1, 12, 2, tzinfo=UTC),
            submission_latency_seconds=1.0,
            fill_latency_seconds=60.0,
            reference_price=Decimal("100.00"),
            fill_price=Decimal("100.10"),
            expected_fill_price=Decimal("100.05"),
            slippage_per_share=Decimal("0.10"),
            slippage_notional=Decimal("1.00"),
            slippage_bps=Decimal("10.0"),
            fill_vs_expected_bps=Decimal("5.0"),
            commission_cost=Decimal("0"),
            spread_cost=Decimal("0.05"),
            slippage_cost=Decimal("1.00"),
            total_cost=Decimal("1.05"),
            policy_mode="TWAP",
            quantity=Decimal("10"),
            is_adverse_fill=True,
            policy_metadata={"policy_mode": "TWAP"},
        )
        db_session.add(row)
        db_session.flush()
        monkeypatch.setattr(execution, "get_session", lambda: db_session)

        assert execution.handle_inspect_fill_quality(_args(intent_id=intent_id)) == 0
        payload = _json_from_output(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["fill_quality"]["intent_id"] == intent_id
        assert Decimal(payload["fill_quality"]["slippage_bps"]) == Decimal("10.0")


class TestPolicyPreview:
    def test_offline_policy_preview_does_not_build_broker(
        self,
        db_session: Session,
        monkeypatch,
        capsys,
    ) -> None:
        intent_id = UUID("00000000-0000-0000-0000-000000000201")
        row = OrderIntents(
            intent_id=intent_id,
            idempotency_key="idem-1",
            run_id=UUID("00000000-0000-0000-0000-000000000301"),
            strategy_id="baseline_strategy",
            timestamp=datetime(2026, 1, 1, 12, tzinfo=UTC),
            bar_timestamp=datetime(2026, 1, 1, 12, tzinfo=UTC),
            symbol="AAPL",
            side=Side.BUY,
            qty=Decimal("10"),
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id="client-order-1",
            meta={},
        )
        db_session.add(row)
        db_session.flush()

        monkeypatch.setattr(execution, "get_session", lambda: db_session)
        monkeypatch.setattr(
            execution,
            "Settings",
            lambda: pytest.fail("offline policy preview should not read broker settings"),
        )

        result = execution.handle_policy_preview(
            _args(
                intent_id=str(intent_id),
                policy_mode="twap",
                offline=True,
                mid_price="185.25",
            )
        )

        assert result == 0
        payload = _json_from_output(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["offline"] is True
        assert payload["policy_mode"] == "twap"
        assert payload["transformed_intent"]["symbol"] == "AAPL"
