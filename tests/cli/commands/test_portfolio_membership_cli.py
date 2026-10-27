"""`atp portfolio membership list|refresh|set` (plan 3.3a operator tooling)."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import portfolio as portfolio_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipRow,
    PortfolioMembershipTransitionRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from tests.conftest import seed_strategy_governance

_NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


class _NoCloseSession:
    """The CLI closes its session; the test transaction must outlive that."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def close(self) -> None:
        pass

    def commit(self) -> None:
        self._session.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


@pytest.fixture
def cli_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr(portfolio_cmd, "get_session", lambda: _NoCloseSession(db_session))
    return db_session


def _args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "json": True,
        "dry_run": False,
        "enforce": False,
        "updated_by": "tristan",
        "environment": "paper",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _out(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    return payload


def _seed_approved(session: Session, strategy_id: str, strategy_type: str) -> None:
    seed_strategy_governance(session, strategy_id=strategy_id)
    session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=f"hash_{strategy_id}",
            config_json={},
            created_at=_NOW,
            strategy_type=strategy_type,
            metadata_json=None,
        )
    )
    session.flush()


class TestParser:
    def test_membership_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["portfolio", "membership", "list", "--json"])
        assert args.func is portfolio_cmd.handle_membership_list

        args = parser.parse_args(
            ["portfolio", "membership", "refresh", "--updated-by", "me", "--enforce"]
        )
        assert args.func is portfolio_cmd.handle_membership_refresh
        assert args.environment == "paper" and args.enforce and not args.dry_run

        args = parser.parse_args(
            [
                "portfolio",
                "membership",
                "set",
                "--strategy-id",
                "macd_crossover_v1",
                "--status",
                "bench",
                "--reason",
                "churn",
                "--updated-by",
                "me",
                "--dry-run",
            ]
        )
        assert args.func is portfolio_cmd.handle_membership_set
        assert args.status == "bench" and args.dry_run

        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "portfolio",
                    "membership",
                    "set",
                    "--strategy-id",
                    "x",
                    "--status",
                    "nope",
                    "--reason",
                    "r",
                    "--updated-by",
                    "me",
                    "--enforce",
                ]
            )


def test_list_shows_every_membership(cli_session: Session, capsys) -> None:
    cli_session.add(
        PortfolioMembershipRow(
            strategy_id="macd_crossover_v1",
            status="bench",
            since=_NOW,
            reason="seed",
            quality_score=None,
            updated_by="tristan",
            updated_at=_NOW,
        )
    )
    cli_session.flush()

    assert portfolio_cmd.handle_membership_list(_args()) == 0
    payload = _out(capsys)
    assert payload["count"] == 1
    assert payload["memberships"][0]["strategy_id"] == "macd_crossover_v1"
    assert payload["memberships"][0]["status"] == "bench"

    assert portfolio_cmd.handle_membership_list(_args(json=False)) == 0
    assert "macd_crossover_v1" in capsys.readouterr().out


def test_set_requires_a_mode_and_records_an_audited_transition(
    cli_session: Session, capsys
) -> None:
    assert (
        portfolio_cmd.handle_membership_set(
            _args(strategy_id="macd_crossover_v1", status="bench", reason="churn")
        )
        == 1
    )
    assert "--dry-run" in capsys.readouterr().out + capsys.readouterr().err

    assert (
        portfolio_cmd.handle_membership_set(
            _args(strategy_id="macd_crossover_v1", status="bench", reason="churn", dry_run=True)
        )
        == 0
    )
    preview = _out(capsys)
    assert preview == {
        "dry_run": True,
        "strategy_id": "macd_crossover_v1",
        "from_status": None,
        "to_status": "bench",
        "reason": "churn",
        "updated_by": "tristan",
        "status": "preview_only",
    }
    assert cli_session.get(PortfolioMembershipRow, "macd_crossover_v1") is None

    assert (
        portfolio_cmd.handle_membership_set(
            _args(strategy_id="macd_crossover_v1", status="bench", reason="churn", enforce=True)
        )
        == 0
    )
    applied = _out(capsys)
    assert applied["status"] == "transitioned"
    assert applied["to_status"] == "bench" and applied["triggered_by"] == "tristan"
    row = cli_session.get(PortfolioMembershipRow, "macd_crossover_v1")
    assert row is not None and row.status == "bench" and row.reason == "churn"
    transitions = (
        cli_session.query(PortfolioMembershipTransitionRow)
        .filter_by(strategy_id="macd_crossover_v1")
        .all()
    )
    assert [t.to_status for t in transitions] == ["bench"]


def test_refresh_previews_then_fills_the_active_set_from_governance(
    cli_session: Session, capsys
) -> None:
    _seed_approved(cli_session, "mean_reversion_v1", "mean_reversion")
    _seed_approved(cli_session, "momentum_v1", "momentum")

    assert portfolio_cmd.handle_membership_refresh(_args(dry_run=True)) == 0
    preview = _out(capsys)
    assert preview["status"] == "preview_only"
    assert sorted(preview["eligible"]) == ["mean_reversion_v1", "momentum_v1"]
    assert preview["current"] == []

    assert portfolio_cmd.handle_membership_refresh(_args(enforce=True)) == 0
    applied = _out(capsys)
    assert applied["status"] == "refreshed"
    assert sorted(applied["active"]) == ["mean_reversion_v1", "momentum_v1"]
    rows = {r.strategy_id: r.status for r in cli_session.query(PortfolioMembershipRow).all()}
    assert rows == {"mean_reversion_v1": "active", "momentum_v1": "active"}
    assert all(
        t.triggered_by == "tristan"
        for t in cli_session.query(PortfolioMembershipTransitionRow).all()
    )
