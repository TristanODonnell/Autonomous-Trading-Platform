from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from autonomous_trading_platform.cli.commands import worker as worker_cmd
from autonomous_trading_platform.cli.main import build_parser


class TestWorkerParserCoverage:
    def test_run_registered_with_defaults(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["worker", "run", "--fixture", "fixtures/x.yaml"])
        assert args.func is worker_cmd.handle_run
        assert args.fixture == Path("fixtures/x.yaml")
        assert args.name is None
        assert args.as_of is None
        assert args.no_pull is False
        assert args.no_reset is False
        assert args.resume is False
        assert args.no_upload is False
        assert args.cadence_minutes is None
        assert args.inject_failures is False
        assert args.actor == "worker"

    def test_run_options(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "worker",
                "run",
                "--fixture",
                "f.yaml",
                "--name",
                "six_month",
                "--as-of",
                "2026-10-12",
                "--no-pull",
                "--resume",
                "--no-upload",
                "--cadence-minutes",
                "5",
                "--inject-failures",
                "--actor",
                "nightly",
            ]
        )
        assert args.name == "six_month"
        assert args.as_of == date(2026, 10, 12)
        assert args.no_pull is True and args.resume is True and args.no_upload is True
        assert args.cadence_minutes == 5
        assert args.inject_failures is True
        assert args.actor == "nightly"

        with pytest.raises(SystemExit):
            parser.parse_args(["worker", "run"])  # --fixture is required

    def test_reset_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["worker", "reset"])
        assert args.func is worker_cmd.handle_reset
        assert args.drop_pulled_datasets is False

        args = parser.parse_args(["worker", "reset", "--drop-pulled-datasets"])
        assert args.drop_pulled_datasets is True


def test_run_refuses_outside_backtest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "paper")
    parser = build_parser()
    args = parser.parse_args(["worker", "run", "--fixture", "f.yaml"])

    from autonomous_trading_platform.application.services.worker_state_reset_service import (
        ResetRefusedError,
    )

    with pytest.raises(ResetRefusedError):
        worker_cmd.handle_run(args)
