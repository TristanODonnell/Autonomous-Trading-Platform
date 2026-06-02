from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import diagnostics
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        as_json=False,
        local_only=False,
        include_broker=False,
        output=None,
        section=None,
        limit=10,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _patch_deps(monkeypatch, db_session: Session) -> None:
    monkeypatch.setattr(
        diagnostics,
        "build_dependencies",
        lambda: diagnostics.DiagnosticsCliDependencies(session=db_session),
    )


def _seed_failed_run(db_session: Session, *, error: str = "boom") -> RunManifestRow:
    row = RunManifestRow(
        run_id=uuid4(),
        run_type=RunType.PAPER,
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        environment="paper",
        broker="alpaca",
        broker_account_id="acct-1",
        strategy_id="strat-failing",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000"),
        interval=BarInterval.FIVE_MIN,
        start_date=date(2026, 1, 1),
        end_date=None,
        dataset_version="v1",
        price_basis=PriceBasis.RAW,
        universe_version="v1",
        cost_model=None,
        fill_model=None,
        random_seed=None,
        git_commit="abc1234",
        docker_image=None,
        python_version=None,
        dependency_lock_hash=None,
        notes=None,
        bar_timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        status="failed",
        current_step="evaluation",
        last_successful_step="ingestion",
        error_message=error,
        artifact_manifest=None,
        schema_definition=None,
        governance_state=GovernanceState.PROPOSED,
    )
    db_session.add(row)
    db_session.flush()
    return row


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


class TestParserRegistration:
    def test_snapshot_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "snapshot"])
        assert args.func is diagnostics.handle_snapshot

    def test_snapshot_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "snapshot", "--json"])
        assert args.as_json is True

    def test_snapshot_local_only_flag(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "snapshot", "--local-only"])
        assert args.local_only is True

    def test_snapshot_include_broker_flag(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "snapshot", "--include-broker"])
        assert args.include_broker is True

    def test_snapshot_output_flag(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "snapshot", "--output", "/tmp/snap.json"])
        assert args.output == "/tmp/snap.json"

    def test_snapshot_section_flag(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "snapshot", "--section", "controls"])
        assert args.section == "controls"

    def test_controls_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "controls"])
        assert args.func is diagnostics.handle_controls

    def test_settings_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "settings"])
        assert args.func is diagnostics.handle_settings

    def test_datasets_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "datasets"])
        assert args.func is diagnostics.handle_datasets

    def test_activity_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "activity"])
        assert args.func is diagnostics.handle_activity

    def test_activity_limit_flag(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "activity", "--limit", "5"])
        assert args.limit == 5

    def test_portfolio_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "portfolio"])
        assert args.func is diagnostics.handle_portfolio

    def test_experiments_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "experiments"])
        assert args.func is diagnostics.handle_experiments

    def test_runtime_jobs_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "runtime-jobs"])
        assert args.func is diagnostics.handle_runtime_jobs

    def test_recent_errors_registered(self):
        parser = build_parser()
        args = parser.parse_args(["diagnostics", "recent-errors"])
        assert args.func is diagnostics.handle_recent_errors


# ---------------------------------------------------------------------------
# handle_snapshot
# ---------------------------------------------------------------------------


class TestHandleSnapshot:
    def test_returns_zero_local_only(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_snapshot(_args(local_only=True)) == 0

    def test_json_output_is_parseable(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_snapshot(_args(local_only=True, as_json=True))
        out = capsys.readouterr().out.strip()
        parsed = json.loads(out)
        assert "snapshot_timestamp" in parsed

    def test_human_output_contains_header(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_snapshot(_args(local_only=True))
        assert "RUNTIME SNAPSHOT" in capsys.readouterr().out

    def test_section_filter_controls_only(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_snapshot(_args(local_only=True, section="controls"))
        out = capsys.readouterr().out
        assert "OPERATOR CONTROLS" in out
        assert "DATASETS" not in out

    def test_section_filter_datasets_only(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_snapshot(_args(local_only=True, section="datasets"))
        out = capsys.readouterr().out
        assert "DATASETS" in out
        assert "OPERATOR CONTROLS" not in out

    def test_local_only_does_not_construct_broker(self, db_session: Session, monkeypatch):
        broker_constructed = []

        def _fake_init(self, *args, **kwargs):
            broker_constructed.append(True)
            raise AssertionError("broker client should not be constructed in local_only mode")

        monkeypatch.setattr(
            "autonomous_trading_platform.execution.clients.alpaca_broker_client."
            "AlpacaBrokerClient.__init__",
            _fake_init,
        )
        _patch_deps(monkeypatch, db_session)
        # Should complete without error because local_only skips broker construction
        result = diagnostics.handle_snapshot(_args(local_only=True))
        assert result == 0
        assert not broker_constructed

    def test_output_flag_writes_file(self, db_session: Session, monkeypatch, tmp_path):
        _patch_deps(monkeypatch, db_session)
        out_file = tmp_path / "snap.json"
        diagnostics.handle_snapshot(_args(local_only=True, as_json=False, output=str(out_file)))
        assert out_file.exists()
        parsed = json.loads(out_file.read_text())
        assert "snapshot_timestamp" in parsed

    def test_output_flag_creates_parent_dirs(self, db_session: Session, monkeypatch, tmp_path):
        _patch_deps(monkeypatch, db_session)
        out_file = tmp_path / "nested" / "dir" / "snap.json"
        diagnostics.handle_snapshot(_args(local_only=True, output=str(out_file)))
        assert out_file.exists()

    def test_degraded_snapshot_returns_zero(self, db_session: Session, monkeypatch):
        """Service-level failures inside section methods are swallowed; CLI still returns 0."""
        from autonomous_trading_platform.application.services.operator_settings_service import (
            OperatorSettingsService,
        )

        def _broken_get_settings(self):
            raise RuntimeError("DB error")

        monkeypatch.setattr(OperatorSettingsService, "get_settings", _broken_get_settings)
        _patch_deps(monkeypatch, db_session)
        result = diagnostics.handle_snapshot(_args(local_only=True))
        assert result == 0


# ---------------------------------------------------------------------------
# handle_controls
# ---------------------------------------------------------------------------


class TestHandleControls:
    def test_returns_zero(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_controls(_args()) == 0

    def test_output_contains_trading_status(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_controls(_args())
        assert "Trading Status" in capsys.readouterr().out

    def test_output_contains_strategy_controls(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_controls(_args())
        assert "STRATEGY CONTROLS" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# handle_settings
# ---------------------------------------------------------------------------


class TestHandleSettings:
    def test_returns_zero(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_settings(_args()) == 0

    def test_output_contains_risk_fields(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_settings(_args())
        out = capsys.readouterr().out
        assert "Max Portfolio DD" in out
        assert "Min Sharpe" in out

    def test_output_contains_governance_fields(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_settings(_args())
        out = capsys.readouterr().out
        assert "Auto-Promote" in out
        assert "Auto-Demote" in out


# ---------------------------------------------------------------------------
# handle_datasets
# ---------------------------------------------------------------------------


class TestHandleDatasets:
    def test_returns_zero(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_datasets(_args()) == 0

    def test_output_contains_dataset_names(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_datasets(_args())
        out = capsys.readouterr().out
        assert "raw_bars" in out
        assert "adjusted_bars" in out
        assert "features" in out
        assert "feature_version" in out

    def test_empty_db_shows_na(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_datasets(_args())
        out = capsys.readouterr().out
        assert "N/A" in out


# ---------------------------------------------------------------------------
# handle_activity
# ---------------------------------------------------------------------------


class TestHandleActivity:
    def test_returns_zero(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_activity(_args(limit=5)) == 0

    def test_empty_activity_shows_none(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_activity(_args(limit=5))
        assert "(none)" in capsys.readouterr().out

    def test_output_contains_activity_header(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_activity(_args(limit=5))
        assert "RECENT ACTIVITY" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# handle_portfolio
# ---------------------------------------------------------------------------


class TestHandlePortfolio:
    def test_returns_zero(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_portfolio(_args()) == 0

    def test_output_contains_portfolio_header(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_portfolio(_args())
        assert "PORTFOLIO" in capsys.readouterr().out

    def test_does_not_call_broker(self, db_session: Session, monkeypatch):
        broker_constructed = []

        def _fake_init(self, *args, **kwargs):
            broker_constructed.append(True)
            raise AssertionError("broker should not be constructed by diagnostics portfolio")

        monkeypatch.setattr(
            "autonomous_trading_platform.execution.clients.alpaca_broker_client."
            "AlpacaBrokerClient.__init__",
            _fake_init,
        )
        _patch_deps(monkeypatch, db_session)
        result = diagnostics.handle_portfolio(_args())
        assert result == 0
        assert not broker_constructed


# ---------------------------------------------------------------------------
# handle_experiments
# ---------------------------------------------------------------------------


class TestHandleExperiments:
    def test_returns_zero(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_experiments(_args(limit=5)) == 0

    def test_empty_shows_none(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_experiments(_args(limit=5))
        assert "(none)" in capsys.readouterr().out

    def test_output_contains_experiments_header(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_experiments(_args(limit=5))
        assert "EXPERIMENTS" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# handle_runtime_jobs
# ---------------------------------------------------------------------------


class TestHandleRuntimeJobs:
    def test_returns_zero(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_runtime_jobs(_args(limit=10)) == 0

    def test_empty_shows_none(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_runtime_jobs(_args(limit=10))
        assert "(none)" in capsys.readouterr().out

    def test_output_contains_jobs_header(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_runtime_jobs(_args(limit=10))
        assert "RUNTIME JOBS" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# handle_recent_errors
# ---------------------------------------------------------------------------


class TestHandleRecentErrors:
    def test_returns_zero_empty(self, db_session: Session, monkeypatch):
        _patch_deps(monkeypatch, db_session)
        assert diagnostics.handle_recent_errors(_args(limit=10)) == 0

    def test_empty_shows_none(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_recent_errors(_args(limit=10))
        assert "(none)" in capsys.readouterr().out

    def test_output_contains_errors_header(self, db_session: Session, monkeypatch, capsys):
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_recent_errors(_args(limit=10))
        assert "RECENT ERRORS" in capsys.readouterr().out

    def test_shows_seeded_failed_run(self, db_session: Session, monkeypatch, capsys):
        _seed_failed_run(db_session, error="index out of range")
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_recent_errors(_args(limit=10))
        out = capsys.readouterr().out
        assert "strat-failing" in out

    def test_limit_respected(self, db_session: Session, monkeypatch, capsys):
        for i in range(5):
            _seed_failed_run(db_session, error=f"err-{i}")
        _patch_deps(monkeypatch, db_session)
        diagnostics.handle_recent_errors(_args(limit=2))
        out = capsys.readouterr().out
        assert out.count("strat-failing") == 2


# ---------------------------------------------------------------------------
# RuntimeSnapshotService.local_only contract
# ---------------------------------------------------------------------------


class TestRuntimeSnapshotServiceLocalOnly:
    def test_local_only_skips_broker_construction(self, db_session: Session, monkeypatch):
        from autonomous_trading_platform.application.services.runtime_snapshot_service import (
            RuntimeSnapshotService,
        )

        broker_inits = []

        def _fake_init(self, *args, **kwargs):
            broker_inits.append(True)
            raise AssertionError("broker must not be constructed in local_only mode")

        monkeypatch.setattr(
            "autonomous_trading_platform.execution.clients.alpaca_broker_client."
            "AlpacaBrokerClient.__init__",
            _fake_init,
        )

        svc = RuntimeSnapshotService(session=db_session, local_only=True)
        svc.capture()
        assert not broker_inits

    def test_section_filter_skips_unchecked_services(self, db_session: Session, monkeypatch):
        from autonomous_trading_platform.application.services import runtime_snapshot_service

        settings_calls = []
        original = runtime_snapshot_service.RuntimeSnapshotService._capture_settings

        def _tracked(self):
            settings_calls.append(True)
            return original(self)

        monkeypatch.setattr(
            runtime_snapshot_service.RuntimeSnapshotService,
            "_capture_settings",
            _tracked,
        )

        svc = runtime_snapshot_service.RuntimeSnapshotService(session=db_session, local_only=True)
        svc.capture(sections=frozenset({"controls"}))
        assert not settings_calls

    def test_full_capture_calls_all_sections(self, db_session: Session):
        from autonomous_trading_platform.application.services.runtime_snapshot_service import (
            RuntimeSnapshotService,
        )

        svc = RuntimeSnapshotService(session=db_session, local_only=True)
        snap = svc.capture()
        assert snap.snapshot_timestamp is not None
        assert snap.datasets is not None
        assert snap.recent_activity is not None
