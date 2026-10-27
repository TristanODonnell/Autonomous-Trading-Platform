from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from decimal import Decimal
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


# ---------------------------------------------------------------- bench-resim (3.3a)


class TestBenchResim:
    def test_parser(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["worker", "bench-resim"])
        assert args.func is worker_cmd.handle_bench_resim
        assert args.run_id is None and args.as_of is None and args.trading_days is None
        assert args.no_pull is False and args.no_publish is False and args.out is None

        args = parser.parse_args(
            [
                "worker",
                "bench-resim",
                "--run-id",
                "w1",
                "--as-of",
                "2026-10-23",
                "--trading-days",
                "21",
                "--no-pull",
                "--no-publish",
                "--out",
                "p.json",
            ]
        )
        assert args.run_id == "w1" and args.as_of == date(2026, 10, 23)
        assert args.trading_days == 21 and args.no_pull and args.no_publish
        assert args.out == Path("p.json")

    def test_default_payload_path_is_under_the_mounted_artifacts_folder(self) -> None:
        assert worker_cmd.default_bench_resims_path("bench_resims_2026-10-26") == Path(
            "artifacts/worker/bench_resims/bench_resims_2026-10-26.json"
        )

    @staticmethod
    def _args(**overrides):
        base = dict(
            run_id=None,
            as_of=date(2026, 10, 23),
            trading_days=None,
            no_pull=True,
            no_publish=False,
            out=None,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    @staticmethod
    def _inputs(**overrides):
        from autonomous_trading_platform.contracts.governance.worker_results import (
            BenchResimInputs,
            BenchResimStrategyInput,
        )

        base = dict(
            as_of=date(2026, 10, 23),
            git_sha="abc1234",
            trading_days=63,
            window_start=date(2026, 7, 27),
            window_end=date(2026, 10, 23),
            dataset_version="raw_bars_x",
            price_basis="raw",
            symbols=["AAPL"],
            strategies=[
                BenchResimStrategyInput(
                    strategy_id="momentum_v1",
                    tier="active",
                    strategy_type="momentum",
                    parameters={"lookback": 5},
                )
            ],
        )
        base.update(overrides)
        return BenchResimInputs(**base)

    def _wire(self, monkeypatch, inputs, *, decision_status="accepted"):
        from autonomous_trading_platform.application.services import worker_result_client as wrc
        from autonomous_trading_platform.contracts.governance.worker_results import (
            WorkerResultDecision,
            WorkerResultStatus,
            WorkerResultType,
        )

        published: list = []

        class FakeClient:
            @classmethod
            def from_env(cls):
                return cls()

            def fetch_bench_inputs(self, *, as_of=None, trading_days=None):
                return inputs

            def publish(self, submission):
                published.append(submission)
                return WorkerResultDecision(
                    run_id=submission.run_id,
                    result_type=WorkerResultType.BENCH_RESIMS,
                    status=WorkerResultStatus(decision_status),
                    reason=None if decision_status == "accepted" else "stale_dataset",
                    received_at=datetime(2026, 10, 23, 2, 0, tzinfo=UTC),
                )

        monkeypatch.setattr(wrc, "WorkerResultClient", FakeClient)
        monkeypatch.setattr(worker_cmd.WorkerStateResetService, "check_environment", lambda: None)
        monkeypatch.setenv("GIT_SHA", "abc1234")
        return published

    def test_nothing_to_do_when_a_has_no_window_or_strategies(self, monkeypatch, capsys) -> None:
        self._wire(monkeypatch, self._inputs(strategies=[]))
        assert worker_cmd.handle_bench_resim(self._args()) == 0
        assert "no_tracked_strategies" in capsys.readouterr().out

        self._wire(
            monkeypatch,
            self._inputs(
                window_start=None,
                window_end=None,
                dataset_version=None,
                window_reason="insufficient_data_for_window",
            ),
        )
        assert worker_cmd.handle_bench_resim(self._args()) == 0
        assert "insufficient_data_for_window" in capsys.readouterr().out

    def test_resimulates_writes_payload_and_publishes(self, monkeypatch, tmp_path, capsys) -> None:
        from autonomous_trading_platform.contracts.governance.worker_results import (
            BenchResimsPayload,
            ResimOutcomePayload,
        )

        published = self._wire(monkeypatch, self._inputs())
        seen: dict = {}

        def fake_resim(inputs, *, run_id):
            seen["run_id"] = run_id
            seen["strategies"] = [s.strategy_id for s in inputs.strategies]
            return BenchResimsPayload(
                window_start=inputs.window_start,
                window_end=inputs.window_end,
                dataset_version=inputs.dataset_version,
                outcomes=[ResimOutcomePayload(strategy_id="momentum_v1", score=Decimal("1.5"))],
            )

        monkeypatch.setattr(worker_cmd, "_resimulate_inputs", fake_resim)
        out = tmp_path / "bench.json"

        code = worker_cmd.handle_bench_resim(self._args(out=out))

        assert code == 0
        assert seen == {"run_id": "bench_resims_2026-10-23", "strategies": ["momentum_v1"]}
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["outcomes"][0]["strategy_id"] == "momentum_v1"
        [submission] = published
        assert submission.run_id == "bench_resims_2026-10-23"
        assert submission.result_type.value == "bench_resims"
        assert submission.dataset_as_of == date(2026, 10, 23)
        assert submission.dataset_versions == ["raw_bars_x"]
        assert submission.git_sha == "abc1234"
        assert submission.payload["outcomes"][0]["score"] == "1.5"
        assert '"status": "accepted"' in capsys.readouterr().out

    def test_rejection_exits_2_and_no_publish_exits_0(self, monkeypatch, tmp_path) -> None:
        from autonomous_trading_platform.contracts.governance.worker_results import (
            BenchResimsPayload,
            ResimOutcomePayload,
        )

        payload = BenchResimsPayload(
            window_start=date(2026, 7, 27),
            window_end=date(2026, 10, 23),
            outcomes=[ResimOutcomePayload(strategy_id="momentum_v1")],
        )
        monkeypatch.setattr(worker_cmd, "_resimulate_inputs", lambda inputs, *, run_id: payload)

        published = self._wire(monkeypatch, self._inputs(), decision_status="rejected")
        assert worker_cmd.handle_bench_resim(self._args(out=tmp_path / "a.json")) == 2
        assert len(published) == 1

        published = self._wire(monkeypatch, self._inputs())
        assert (
            worker_cmd.handle_bench_resim(self._args(out=tmp_path / "b.json", no_publish=True)) == 0
        )
        assert published == []
