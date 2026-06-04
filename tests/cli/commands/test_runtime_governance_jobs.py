from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

from autonomous_trading_platform.cli.commands import runtime
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow


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


def test_runtime_replay_plan_accepts_json_flag() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "runtime",
            "replay-plan",
            "--symbols",
            "AAPL,MSFT",
            "--start",
            "2026-05-04T00:00:00Z",
            "--end",
            "2026-05-08T00:00:00Z",
            "--cycles",
            "market_backfill,features,trading",
            "--max-ticks",
            "5",
            "--json",
        ]
    )

    assert args.func is runtime.handle_replay_plan
    assert args.json is True


def test_run_type_models_persist_enum_values() -> None:
    expected = [member.value if member is RunType.GOVERNANCE else member.name for member in RunType]

    assert RunManifestRow.__table__.c.run_type.type.enums == expected
    assert IngestionRuns.__table__.c.run_type.type.enums == expected


def test_runtime_trigger_job_json_omits_header(monkeypatch, capsys) -> None:
    class FakeManualTriggerService:
        def __init__(self, dispatchers):
            self.dispatchers = dispatchers

        def trigger(self, job_name):
            return SimpleNamespace(job_name=job_name, status="completed", result={"ok": True})

    monkeypatch.setattr(runtime, "ManualTriggerService", FakeManualTriggerService)

    args = argparse.Namespace(
        job_name="strategy_auto_promotion_cycle",
        dry_run=False,
        json=True,
    )

    assert runtime.handle_trigger_job(args) == 0

    out = capsys.readouterr().out
    assert out.lstrip().startswith("{")
    parsed = json.loads(out)
    assert parsed["status"] == "completed"


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


def test_runtime_run_cycle_dry_run_json_omits_header(capsys):
    import argparse

    args = argparse.Namespace(
        timestamp="2026-05-08T15:00:00Z",
        dry_run=True,
        json=True,
    )

    assert runtime.handle_run_cycle(args) == 0

    out = capsys.readouterr().out
    assert out.lstrip().startswith("{")
    parsed = json.loads(out)
    assert parsed["dry_run"] is True
