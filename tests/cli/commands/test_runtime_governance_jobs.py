from __future__ import annotations

import json

from autonomous_trading_platform.cli.commands import runtime
from autonomous_trading_platform.cli.main import build_parser


def test_runtime_trigger_governance_job_command_is_registered() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "runtime",
            "trigger-job",
            "--job-name",
            "strategy_auto_promotion_cycle",
        ]
    )

    assert args.func is runtime.handle_trigger_job
    assert args.job_name == "strategy_auto_promotion_cycle"


# ---------------------------------------------------------------------------
# evaluate-cycle
# ---------------------------------------------------------------------------


class TestEvaluateCycle:
    def test_registered(self):
        parser = build_parser()
        args = parser.parse_args(
            ["runtime", "evaluate-cycle", "--timestamp", "2026-05-26T15:35:00Z"]
        )
        assert args.func is runtime.handle_evaluate_cycle
        assert args.timestamp == "2026-05-26T15:35:00Z"

    def test_dry_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            ["runtime", "evaluate-cycle", "--timestamp", "2026-05-26T15:35:00Z", "--dry-run"]
        )
        assert args.dry_run is True

    def test_dry_run_returns_zero_without_calling_cycle(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(
            runtime, "run_trading_evaluation_cycle", lambda **_: called.append(True)
        )
        import argparse

        args = argparse.Namespace(timestamp="2026-05-26T15:35:00Z", dry_run=True)
        rc = runtime.handle_evaluate_cycle(args)
        assert rc == 0
        assert called == []

    def test_dry_run_output_contains_dry_run_key(self, monkeypatch, capsys):
        monkeypatch.setattr(runtime, "run_trading_evaluation_cycle", lambda **_: None)
        import argparse

        args = argparse.Namespace(timestamp="2026-05-26T15:35:00Z", dry_run=True)
        runtime.handle_evaluate_cycle(args)
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["dry_run"] is True

    def test_dry_run_output_contains_warning(self, monkeypatch, capsys):
        monkeypatch.setattr(runtime, "run_trading_evaluation_cycle", lambda **_: None)
        import argparse

        args = argparse.Namespace(timestamp="2026-05-26T15:35:00Z", dry_run=True)
        runtime.handle_evaluate_cycle(args)
        out = capsys.readouterr().out
        assert "broker" in out.lower()
