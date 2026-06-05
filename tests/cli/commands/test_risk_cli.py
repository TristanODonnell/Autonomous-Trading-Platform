from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import risk as risk_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.runtime.optimizer import OptimizationObjective
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow


def _extract_json(output: str) -> dict[Any, Any]:
    return cast(dict[Any, Any], json.loads(output[output.index("{") :]))


class TestRiskParserCoverage:
    def test_limits_snapshot_exposure_and_concentration_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["risk", "limits", "show", "--json"])
        assert args.func is risk_cmd.handle_limits_show

        args = parser.parse_args(["risk", "snapshot", "latest", "--json"])
        assert args.func is risk_cmd.handle_snapshot_latest

        args = parser.parse_args(["risk", "snapshot", "history", "--limit", "5"])
        assert args.func is risk_cmd.handle_snapshot_history
        assert args.limit == 5

        args = parser.parse_args(
            [
                "risk",
                "snapshot",
                "compute",
                "--run-id",
                "11111111-1111-4111-8111-111111111111",
                "--as-of",
                "2026-05-04T00:00:00Z",
                "--dry-run",
            ]
        )
        assert args.func is risk_cmd.handle_snapshot_compute
        assert args.dry_run is True

        args = parser.parse_args(["risk", "exposure", "current", "--json"])
        assert args.func is risk_cmd.handle_exposure_current

        args = parser.parse_args(["risk", "concentration", "current", "--top", "10"])
        assert args.func is risk_cmd.handle_concentration_current
        assert args.top == 10

    def test_pretrade_and_drawdown_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "risk",
                "pretrade",
                "check",
                "--symbol",
                "AAPL",
                "--side",
                "buy",
                "--qty",
                "10",
                "--limit-price",
                "190",
                "--strategy-id",
                "momentum_v1",
                "--json",
            ]
        )
        assert args.func is risk_cmd.handle_pretrade_check
        assert args.side is Side.BUY
        assert args.qty == Decimal("10")

        args = parser.parse_args(["risk", "drawdown", "list", "--state", "breached", "--json"])
        assert args.func is risk_cmd.handle_drawdown_list

        args = parser.parse_args(["risk", "drawdown", "show", "--strategy-id", "momentum_v1"])
        assert args.func is risk_cmd.handle_drawdown_show

        args = parser.parse_args(
            ["risk", "drawdown", "transitions", "--strategy-id", "momentum_v1", "--limit", "50"]
        )
        assert args.func is risk_cmd.handle_drawdown_transitions

        args = parser.parse_args(["risk", "drawdown", "pending-ack"])
        assert args.func is risk_cmd.handle_drawdown_pending_ack

        args = parser.parse_args(
            [
                "risk",
                "drawdown",
                "acknowledge",
                "--strategy-id",
                "momentum_v1",
                "--operator",
                "risk-manager",
                "--reason",
                "reviewed breach",
            ]
        )
        assert args.func is risk_cmd.handle_drawdown_acknowledge

        args = parser.parse_args(["risk", "drawdown", "evaluate", "--dry-run"])
        assert args.func is risk_cmd.handle_drawdown_evaluate

    def test_budget_factor_correlation_and_optimizer_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "risk",
                "budget",
                "compute",
                "--strategy-id",
                "momentum_v1",
                "--strategy-id",
                "mean_reversion_v1",
                "--mode",
                "equal_risk_contribution",
                "--dry-run",
            ]
        )
        assert args.func is risk_cmd.handle_budget_compute
        assert args.strategy_ids == ["momentum_v1", "mean_reversion_v1"]

        args = parser.parse_args(
            ["risk", "budget", "run", "--strategy-id", "momentum_v1", "--strategy-id", "mr_v1"]
        )
        assert args.func is risk_cmd.handle_budget_run

        args = parser.parse_args(["risk", "budget", "latest", "--json"])
        assert args.func is risk_cmd.handle_budget_latest

        args = parser.parse_args(
            ["risk", "factor", "latest", "--portfolio-id", "default", "--lookback-window", "60"]
        )
        assert args.func is risk_cmd.handle_factor_latest

        args = parser.parse_args(
            [
                "risk",
                "factor",
                "history",
                "--since",
                "2026-05-01T00:00:00Z",
                "--portfolio-id",
                "default",
                "--limit",
                "50",
            ]
        )
        assert args.func is risk_cmd.handle_factor_history

        args = parser.parse_args(
            ["risk", "factor", "run", "--as-of", "2026-05-04T00:00:00Z", "--dry-run"]
        )
        assert args.func is risk_cmd.handle_factor_run

        args = parser.parse_args(["risk", "correlation", "current", "--json"])
        assert args.func is risk_cmd.handle_correlation_current

        args = parser.parse_args(
            [
                "risk",
                "mv-optimize",
                "--strategy-id",
                "momentum_v1",
                "--strategy-id",
                "mean_reversion_v1",
                "--objective",
                "min_variance",
                "--dry-run",
            ]
        )
        assert args.func is risk_cmd.handle_mv_optimize

    def test_verify_audit_and_export_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "risk",
                "verify-parameter-effects",
                "--controls",
                "fixtures/controls.yaml",
                "--settings",
                "fixtures/settings.yaml",
                "--symbols",
                "SPY,QQQ",
                "--start",
                "2026-01-01",
                "--end",
                "2026-03-31",
                "--parameter",
                "max_strategy_drawdown",
                "--output",
                "artifacts/risk/parameter-effects.json",
            ]
        )
        assert args.func is risk_cmd.handle_verify_parameter_effects
        assert args.controls == Path("fixtures/controls.yaml")
        assert args.output == Path("artifacts/risk/parameter-effects.json")

        args = parser.parse_args(["risk", "audit-log", "--limit", "20"])
        assert args.func is risk_cmd.handle_audit_log

        args = parser.parse_args(["risk", "export", "--output", "artifacts/risk/current.json"])
        assert args.func is risk_cmd.handle_export


class TestRiskHelpers:
    def test_objective_alias(self) -> None:
        assert risk_cmd._parse_objective("min_variance") is OptimizationObjective.MINIMUM_VARIANCE

    def test_positive_int_validation(self) -> None:
        assert risk_cmd._positive_int("1") == 1


def test_audit_log_lists_emitted_risk_event_names(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(risk_cmd, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            AuditLogRow(
                event_id=str(uuid4()),
                event_type="DRAWDOWN_LIMIT_BREACHED",
                component="system_drawdown_governance",
                event_timestamp=now,
                message="drawdown breach",
                metadata_={"strategy_id": "momentum_v1"},
            ),
            AuditLogRow(
                event_id=str(uuid4()),
                event_type="PORTFOLIO_SYMBOL_EXPOSURE_BLOCKED",
                component="pre_trade_risk",
                event_timestamp=now,
                message="symbol exposure blocked",
                metadata_={"strategy_id": "momentum_v1"},
            ),
            AuditLogRow(
                event_id=str(uuid4()),
                event_type="UNRELATED_EVENT",
                component="other",
                event_timestamp=now,
                message="ignore",
                metadata_={},
            ),
        ]
    )
    db_session.flush()

    assert risk_cmd.handle_audit_log(argparse.Namespace(limit=20)) == 0
    payload = _extract_json(capsys.readouterr().out)

    event_types = {event["event_type"] for event in payload["events"]}
    assert "DRAWDOWN_LIMIT_BREACHED" in event_types
    assert "PORTFOLIO_SYMBOL_EXPOSURE_BLOCKED" in event_types
    assert "UNRELATED_EVENT" not in event_types
