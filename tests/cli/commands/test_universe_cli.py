from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autonomous_trading_platform.application.services.universe_timeline_service import (
    UniverseTimelineError,
)
from autonomous_trading_platform.cli.commands import universe as universe_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UniverseTimelineBuildResult,
    UniverseTimelineExportResult,
    UniverseTimelineExportStep,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
    UniverseTimelineRules,
    UniverseTimelineStep,
)
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationValidationError,
)


class TestUniverseParserRegistration:
    def test_select_now_dry_run_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "select-now", "--dry-run"])
        assert args.func is universe_cmd.handle_select_now
        assert args.dry_run is True

    def test_inspect_version_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "inspect-version", "--version-id", "uv-1"])
        assert args.func is universe_cmd.handle_inspect_version
        assert args.version_id == "uv-1"

    def test_validate_version_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "validate-version", "--version-id", "uv-1"])
        assert args.func is universe_cmd.handle_validate_version

    def test_seed_dry_run_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "seed", "--symbols", "SPY,AAPL", "--dry-run"])
        assert args.func is universe_cmd.handle_seed
        assert args.dry_run is True

    def test_raw_pool_refresh_dry_run_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "raw-pool-refresh", "--dry-run"])
        assert args.func is universe_cmd.handle_raw_pool_refresh
        assert args.dry_run is True

    def test_raw_pool_history_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "raw-pool-history", "--limit", "5"])
        assert args.func is universe_cmd.handle_raw_pool_history
        assert args.limit == 5

    def test_raw_pool_inspect_snapshot_id_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "raw-pool-inspect", "--snapshot-id", "snapshot-1"])
        assert args.snapshot_id == "snapshot-1"

    def test_candidate_generate_dry_run_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "candidate-generate", "--dry-run"])
        assert args.func is universe_cmd.handle_candidate_generate
        assert args.dry_run is True

    def test_export_version_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["universe", "export-version", "--version-id", "uv-1", "--output", "uv.json"]
        )
        assert args.func is universe_cmd.handle_export_version

    def test_import_version_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "import-version", "--input", "uv.json", "--dry-run"])
        assert args.func is universe_cmd.handle_import_version
        assert args.dry_run is True

    def test_lifecycle_symbol_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "lifecycle-symbol", "--symbol", "AAPL"])
        assert args.func is universe_cmd.handle_lifecycle_symbol

    def test_inspect_rebalance_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["universe", "inspect-rebalance", "--rebalance-run-id", "rebalance-1"]
        )
        assert args.func is universe_cmd.handle_inspect_rebalance

    def test_inspect_rotation_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "inspect-rotation", "--rotation-id", "rotation-1"])
        assert args.func is universe_cmd.handle_inspect_rotation


class TestUniverseParserValidation:
    def test_limit_must_be_positive(self) -> None:
        parser = build_parser()
        try:
            parser.parse_args(["universe", "history", "--limit", "0"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse validation failure")

    def test_churn_pct_must_be_between_zero_and_one(self) -> None:
        parser = build_parser()
        try:
            parser.parse_args(["universe", "rotate", "--max-churn-pct", "1.5"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse validation failure")

    def test_decimal_filters_must_be_positive(self) -> None:
        parser = build_parser()
        try:
            parser.parse_args(["universe", "candidate-generate", "--min-price", "0"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse validation failure")


# ---------------------------------------------------------------------------
# Finding 16: `atp universe monthly-rotation --dry-run|--enforce`
# ---------------------------------------------------------------------------


class TestMonthlyRotationCli:
    def test_registered_with_scheduler_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["universe", "monthly-rotation", "--dry-run"])
        assert args.func is universe_cmd.handle_monthly_rotation
        assert args.market_data == "alpaca_daily"
        assert args.screener_source == "alpaca_active"
        assert args.dry_run and not args.enforce and not args.force

    def test_candidate_pool_can_be_the_index_as_of_the_timestamp(self) -> None:
        base = ["universe", "monthly-rotation", "--dry-run", "--screener-source"]
        args = build_parser().parse_args([*base, "sp500_point_in_time"])
        assert args.screener_source == "sp500_point_in_time"
        with pytest.raises(SystemExit):
            build_parser().parse_args([*base, "todays_favourites"])

    def test_requires_dry_run_or_enforce(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["universe", "monthly-rotation"])
        with pytest.raises(SystemExit):
            build_parser().parse_args(["universe", "monthly-rotation", "--dry-run", "--enforce"])

    def test_candidate_generate_accepts_market_data(self) -> None:
        args = build_parser().parse_args(
            ["universe", "candidate-generate", "--dry-run", "--market-data", "alpaca_daily"]
        )
        assert args.market_data == "alpaca_daily"

    @pytest.mark.parametrize(
        ("mode", "status", "expect_commit", "expect_exit"),
        [
            ("dry_run", "ok", False, 0),
            ("enforce", "ok", True, 0),
            ("enforce", "failed", False, 1),
        ],
    )
    def test_handler_runs_the_scheduler_hook_and_keeps_only_enforced_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        mode: str,
        status: str,
        expect_commit: bool,
        expect_exit: int,
    ) -> None:
        calls: dict[str, Any] = {}
        session = SimpleNamespace(
            commit=lambda: calls.setdefault("commit", True),
            rollback=lambda: calls.setdefault("rollback", True),
            close=lambda: calls.setdefault("close", True),
        )
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        def fake_hook(**kwargs: Any) -> Any:
            calls["hook"] = kwargs
            return SimpleNamespace(
                status=status,
                errors=["boom"] if status == "failed" else [],
                warnings=[],
                summary={"added": ["NVDA"], "removed": [], "churn_pct": 0.05}
                if status == "ok"
                else {},
            )

        monkeypatch.setattr(universe_cmd, "run_universe_at_timestamp", fake_hook)

        args = argparse.Namespace(
            timestamp="2026-10-30T22:00:00Z",
            market_data="alpaca_daily",
            dataset_version_id=None,
            force=False,
            approved_by="tristan",
            dry_run=mode == "dry_run",
            enforce=mode == "enforce",
        )
        exit_code = universe_cmd.handle_monthly_rotation(args)

        assert exit_code == expect_exit
        hook = calls["hook"]
        assert hook["session"] is session
        assert hook["timestamp"] == datetime(2026, 10, 30, 22, 0, tzinfo=UTC)
        assert hook["force_rotation"] is False
        assert hook["rotation_reason"] == "operator_monthly"
        assert hook["candidate_market_data"] == "alpaca_daily"
        # No option given: today's active names, what the scheduler's step uses.
        assert hook["screener_source"] == "alpaca_active"
        assert hook["replay_context"].actor == "tristan"
        assert calls.get("commit", False) is expect_commit
        assert calls.get("rollback", False) is (not expect_commit)
        assert calls["close"] is True

        out = capsys.readouterr().out
        payload = json.loads(out[out.index("{") :])
        assert payload["dry_run"] is (mode == "dry_run")
        assert payload["rotation_kept"] is expect_commit
        assert payload["status"] == status
        if status == "ok":
            assert payload["added"] == ["NVDA"]
        else:
            assert payload["errors"] == ["boom"]

    def test_handler_passes_the_point_in_time_pool_for_an_old_date(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: dict[str, Any] = {}
        session = SimpleNamespace(commit=lambda: None, rollback=lambda: None, close=lambda: None)
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        def fake_hook(**kwargs: Any) -> Any:
            calls["hook"] = kwargs
            return SimpleNamespace(status="ok", errors=[], warnings=[], summary={})

        monkeypatch.setattr(universe_cmd, "run_universe_at_timestamp", fake_hook)

        args = build_parser().parse_args(
            [
                "universe",
                "monthly-rotation",
                "--dry-run",
                "--timestamp",
                "2024-11-18T14:30:00Z",
                "--screener-source",
                "sp500_point_in_time",
            ]
        )
        assert universe_cmd.handle_monthly_rotation(args) == 0

        assert calls["hook"]["screener_source"] == "sp500_point_in_time"
        assert calls["hook"]["timestamp"] == datetime(2024, 11, 18, 14, 30, tzinfo=UTC)
        out = capsys.readouterr().out
        assert json.loads(out[out.index("{") :])["screener_source"] == "sp500_point_in_time"

    def test_bar_coverage_minimum_is_off_unless_given(self) -> None:
        base = ["universe", "monthly-rotation", "--dry-run"]
        assert build_parser().parse_args(base).min_bar_coverage is None
        args = build_parser().parse_args([*base, "--min-bar-coverage", "0.70"])
        assert args.min_bar_coverage == 0.70
        for not_a_fraction in ("70", "0", "-0.1"):
            with pytest.raises(SystemExit):
                build_parser().parse_args([*base, "--min-bar-coverage", not_a_fraction])

    @pytest.mark.parametrize(
        ("option", "expected"), [([], None), (["--min-bar-coverage", "0.7"], 0.7)]
    )
    def test_handler_passes_the_bar_coverage_minimum(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        option: list[str],
        expected: float | None,
    ) -> None:
        calls: dict[str, Any] = {}
        session = SimpleNamespace(commit=lambda: None, rollback=lambda: None, close=lambda: None)
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        def fake_hook(**kwargs: Any) -> Any:
            calls["hook"] = kwargs
            return SimpleNamespace(status="ok", errors=[], warnings=[], summary={})

        monkeypatch.setattr(universe_cmd, "run_universe_at_timestamp", fake_hook)

        args = build_parser().parse_args(["universe", "monthly-rotation", "--dry-run", *option])
        assert universe_cmd.handle_monthly_rotation(args) == 0

        assert calls["hook"]["min_intraday_bar_coverage"] == expected
        out = capsys.readouterr().out
        assert json.loads(out[out.index("{") :])["min_bar_coverage"] == expected


class TestHeldCoverageLineCli:
    _BASE = ["universe", "monthly-rotation", "--dry-run"]

    def test_the_lower_line_is_off_unless_given(self) -> None:
        assert build_parser().parse_args(self._BASE).held_min_bar_coverage is None
        args = build_parser().parse_args(
            [*self._BASE, "--min-bar-coverage", "0.70", "--held-min-bar-coverage", "0.55"]
        )
        assert (args.min_bar_coverage, args.held_min_bar_coverage) == (0.70, 0.55)

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            (["--held-min-bar-coverage", "0.55"], "needs --min-bar-coverage"),
            (
                ["--min-bar-coverage", "0.55", "--held-min-bar-coverage", "0.70"],
                "must be at most",
            ),
        ],
    )
    def test_lines_that_do_not_fit_stop_the_command_before_any_work(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        options: list[str],
        message: str,
    ) -> None:
        def _not_reached(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("no session and no provider call for bad options")

        monkeypatch.setattr(universe_cmd, "get_session", _not_reached)

        args = build_parser().parse_args([*self._BASE, *options])
        assert universe_cmd.handle_monthly_rotation(args) == 1
        assert message in capsys.readouterr().out

    def test_handler_passes_the_lower_line_on(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: dict[str, Any] = {}
        session = SimpleNamespace(commit=lambda: None, rollback=lambda: None, close=lambda: None)
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        def fake_hook(**kwargs: Any) -> Any:
            calls["hook"] = kwargs
            return SimpleNamespace(status="ok", errors=[], warnings=[], summary={})

        monkeypatch.setattr(universe_cmd, "run_universe_at_timestamp", fake_hook)

        args = build_parser().parse_args(
            [*self._BASE, "--min-bar-coverage", "0.70", "--held-min-bar-coverage", "0.55"]
        )
        assert universe_cmd.handle_monthly_rotation(args) == 0

        assert calls["hook"]["min_intraday_bar_coverage"] == 0.70
        assert calls["hook"]["held_min_intraday_bar_coverage"] == 0.55
        out = capsys.readouterr().out
        assert json.loads(out[out.index("{") :])["held_min_bar_coverage"] == 0.55


class TestBuildTimelineCli:
    _WINDOW = ["--start", "2024-11-18", "--end", "2025-02-03"]

    def _result(self, status: str) -> UniverseTimelineBuildResult:
        rules = UniverseTimelineRules(
            screener_source="sp500_point_in_time",
            candidate_market_data="alpaca_daily",
            min_intraday_bar_coverage=0.7,
        )
        steps = [
            UniverseTimelineStep(
                timestamp=datetime(2024, 11, 18, tzinfo=UTC),
                kind="start",
                outcome="rotated",
                universe_version_id="uv-1",
                symbols=["AAPL", "MSFT"],
                added=["AAPL", "MSFT"],
                eligible_count=37,
            )
        ]
        errors = []
        if status == "stopped":
            steps.append(
                UniverseTimelineStep(
                    timestamp=datetime(2024, 11, 29, 23, tzinfo=UTC),
                    kind="month_end",
                    outcome="failed",
                    detail="screener_refresh_failed: boom",
                )
            )
            errors = ["2024-11-29T23:00:00+00:00: screener_refresh_failed: boom"]
        return UniverseTimelineBuildResult(
            start=date(2024, 11, 18),
            end=date(2025, 2, 3),
            rules=rules,
            status=status,  # type: ignore[arg-type]
            steps=steps,
            symbols_union=["AAPL", "MSFT"],
            errors=errors,
        )

    def test_a_mode_is_required_and_the_pool_defaults_to_point_in_time(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["universe", "build-timeline", *self._WINDOW])
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["universe", "build-timeline", *self._WINDOW, "--plan", "--enforce"]
            )
        args = build_parser().parse_args(["universe", "build-timeline", *self._WINDOW, "--plan"])
        assert (args.start, args.end) == (date(2024, 11, 18), date(2025, 2, 3))
        assert args.screener_source == "sp500_point_in_time"
        assert args.market_data == "alpaca_daily"
        assert args.min_bar_coverage is None

    @pytest.mark.parametrize(
        ("mode", "status", "expect_exit"),
        [("--plan", "planned", 0), ("--enforce", "complete", 0), ("--enforce", "stopped", 1)],
    )
    def test_handler_builds_with_the_given_rules_and_reports_each_step(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        mode: str,
        status: str,
        expect_exit: int,
    ) -> None:
        calls: dict[str, Any] = {}
        session = SimpleNamespace(
            rollback=lambda: None, close=lambda: calls.setdefault("close", True)
        )
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        def fake_build(**kwargs: Any) -> UniverseTimelineBuildResult:
            calls["build"] = kwargs
            return self._result(status)

        monkeypatch.setattr(universe_cmd, "build_universe_timeline", fake_build)

        args = build_parser().parse_args(
            ["universe", "build-timeline", *self._WINDOW, mode, "--min-bar-coverage", "0.70"]
        )
        assert universe_cmd.handle_build_timeline(args) == expect_exit

        built = calls["build"]
        assert built["session"] is session
        assert (built["start"], built["end"]) == (date(2024, 11, 18), date(2025, 2, 3))
        assert built["plan_only"] is (mode == "--plan")
        assert built["rules"] == UniverseTimelineRules(
            screener_source="sp500_point_in_time",
            candidate_market_data="alpaca_daily",
            min_intraday_bar_coverage=0.7,
        )
        assert calls["close"] is True

        out = capsys.readouterr().out
        payload = json.loads(out[out.index("{") :])
        assert payload["status"] == status
        assert payload["rules"]["min_intraday_bar_coverage"] == 0.7
        assert payload["steps"][0]["symbol_count"] == 2
        assert payload["steps"][0]["eligible_count"] == 37
        assert payload["symbols_union"] == ["AAPL", "MSFT"]
        assert payload["symbols_union_count"] == 2
        assert len(payload["errors"]) == (1 if status == "stopped" else 0)

    def test_a_refused_build_exits_1_and_says_why(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        session = SimpleNamespace(rollback=lambda: None, close=lambda: None)
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        def refuse(**kwargs: Any) -> UniverseTimelineBuildResult:
            raise UniverseTimelineError("2 universe version(s) were not made by this timeline")

        monkeypatch.setattr(universe_cmd, "build_universe_timeline", refuse)

        args = build_parser().parse_args(["universe", "build-timeline", *self._WINDOW, "--enforce"])
        assert universe_cmd.handle_build_timeline(args) == 1
        assert "were not made by this timeline" in capsys.readouterr().out


class TestBuildTimelineHeldCoverageCli:
    _BASE = ["universe", "build-timeline", "--start", "2024-11-18", "--end", "2025-02-03"]

    def test_the_lower_line_is_off_unless_given(self) -> None:
        assert build_parser().parse_args([*self._BASE, "--plan"]).held_min_bar_coverage is None

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            (["--held-min-bar-coverage", "0.55"], "needs --min-bar-coverage"),
            (
                ["--min-bar-coverage", "0.55", "--held-min-bar-coverage", "0.70"],
                "must be at most",
            ),
        ],
    )
    def test_lines_that_do_not_fit_stop_the_build_before_any_work(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        options: list[str],
        message: str,
    ) -> None:
        def _not_reached(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("no session and no provider call for bad options")

        monkeypatch.setattr(universe_cmd, "get_session", _not_reached)

        args = build_parser().parse_args([*self._BASE, "--enforce", *options])
        assert universe_cmd.handle_build_timeline(args) == 1
        assert message in capsys.readouterr().out

    def test_the_lower_line_is_part_of_the_rules_the_build_gets(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: dict[str, Any] = {}
        session = SimpleNamespace(rollback=lambda: None, close=lambda: None)
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        def fake_build(**kwargs: Any) -> UniverseTimelineBuildResult:
            calls["build"] = kwargs
            return UniverseTimelineBuildResult(
                start=kwargs["start"], end=kwargs["end"], rules=kwargs["rules"], status="planned"
            )

        monkeypatch.setattr(universe_cmd, "build_universe_timeline", fake_build)

        args = build_parser().parse_args(
            [*self._BASE, "--plan", "--min-bar-coverage", "0.70", "--held-min-bar-coverage", "0.55"]
        )
        assert universe_cmd.handle_build_timeline(args) == 0

        assert calls["build"]["rules"].held_min_intraday_bar_coverage == 0.55
        out = capsys.readouterr().out
        assert json.loads(out[out.index("{") :])["rules"]["held_min_intraday_bar_coverage"] == 0.55


class TestExportTimelineCli:
    _T_START = datetime(2024, 11, 18, tzinfo=UTC)
    _T_NOV = datetime(2024, 11, 29, 23, tzinfo=UTC)

    def _result(self, status: str) -> UniverseTimelineExportResult:
        if status == "refused":
            return UniverseTimelineExportResult(
                status="refused", errors=["list v2 ends at X and the next begins at Y: a gap"]
            )
        return UniverseTimelineExportResult(
            status=status,  # type: ignore[arg-type]
            dataset_version_id=None if status == "planned" else "universe_timeline_1",
            window_start=date(2024, 11, 18),
            rules={"window_start": "2024-11-18", "screener_source": "sp500_point_in_time"},
            first_step_at=self._T_START,
            last_step_at=self._T_NOV,
            steps=[
                UniverseTimelineExportStep(
                    timestamp=self._T_START, outcome="rotated", universe_version_id="v1"
                ),
                UniverseTimelineExportStep(
                    timestamp=self._T_NOV, outcome="unchanged", universe_version_id="v1"
                ),
            ],
            periods=[
                UniverseTimelinePeriod(
                    universe_version_id="v1",
                    effective_from=self._T_START,
                    members=[
                        UniverseTimelineMember(symbol="MSFT", rank=2),
                        UniverseTimelineMember(symbol="AAPL", rank=1),
                    ],
                )
            ],
            symbols_union=["AAPL", "MSFT"],
            rows=2,
            content_hash="abc123",
        )

    def _patch(
        self, monkeypatch: pytest.MonkeyPatch, outcome: UniverseTimelineExportResult | Exception
    ) -> dict[str, Any]:
        calls: dict[str, Any] = {"commit": 0, "rollback": 0, "close": 0}

        def count(name: str) -> None:
            calls[name] += 1

        session = SimpleNamespace(
            commit=lambda: count("commit"),
            rollback=lambda: count("rollback"),
            close=lambda: count("close"),
        )
        calls["session"] = session
        monkeypatch.setattr(universe_cmd, "get_session", lambda: session)

        class _FakeExport:
            def __init__(self, session: Any, *, base_path: Any = None) -> None:
                calls["init"] = (session, base_path)

            def export(self, *, now_utc: datetime, plan_only: bool = False) -> Any:
                calls["export"] = {"now_utc": now_utc, "plan_only": plan_only}
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        monkeypatch.setattr(universe_cmd, "UniverseTimelineExportService", _FakeExport)
        return calls

    def test_a_mode_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["universe", "export-timeline"])
        with pytest.raises(SystemExit):
            build_parser().parse_args(["universe", "export-timeline", "--plan", "--enforce"])
        args = build_parser().parse_args(["universe", "export-timeline", "--plan"])
        assert args.plan is True and args.data_root is None

    @pytest.mark.parametrize(
        ("mode", "status"),
        [("--plan", "planned"), ("--enforce", "created"), ("--enforce", "up_to_date")],
    )
    def test_handler_exports_and_reports_the_lists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        mode: str,
        status: str,
    ) -> None:
        calls = self._patch(monkeypatch, self._result(status))

        args = build_parser().parse_args(
            ["universe", "export-timeline", mode, "--data-root", str(tmp_path)]
        )
        assert universe_cmd.handle_export_timeline(args) == 0

        assert calls["init"] == (calls["session"], tmp_path)
        assert calls["export"]["plan_only"] is (mode == "--plan")
        assert calls["export"]["now_utc"].tzinfo is not None
        assert (calls["commit"], calls["rollback"], calls["close"]) == (1, 0, 1)

        out = capsys.readouterr().out
        payload = json.loads(out[out.index("{") :])
        assert payload["status"] == status
        assert payload["dataset_version_id"] == (
            None if status == "planned" else "universe_timeline_1"
        )
        assert payload["window_start"] == "2024-11-18"
        assert payload["step_count"] == 2
        assert payload["steps_by_outcome"] == {"rotated": 1, "unchanged": 1}
        assert payload["periods"] == [
            {
                "universe_version_id": "v1",
                "effective_from": "2024-11-18T00:00:00+00:00",
                "effective_to": None,
                "symbol_count": 2,
                "symbols": ["AAPL", "MSFT"],
            }
        ]
        assert payload["symbols_union_count"] == 2
        assert payload["content_hash"] == "abc123"
        assert payload["errors"] == []

    def test_a_refused_timeline_exits_1_and_says_why(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._patch(monkeypatch, self._result("refused"))

        args = build_parser().parse_args(["universe", "export-timeline", "--enforce"])
        assert universe_cmd.handle_export_timeline(args) == 1

        out = capsys.readouterr().out
        assert json.loads(out[out.index("{") : out.rindex("}") + 1])["status"] == "refused"
        assert "Timeline not exported: list v2 ends at X" in out

    @pytest.mark.parametrize(
        ("error", "says"),
        [
            (OSError("disk full"), "disk full"),
            (
                DatasetRegistrationValidationError(["schema_version 9 is not supported"]),
                "schema_version 9 is not supported",
            ),
        ],
    )
    def test_a_failed_write_rolls_back_and_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: Exception,
        says: str,
    ) -> None:
        calls = self._patch(monkeypatch, error)

        args = build_parser().parse_args(["universe", "export-timeline", "--enforce"])
        assert universe_cmd.handle_export_timeline(args) == 1

        assert (calls["commit"], calls["rollback"], calls["close"]) == (0, 1, 1)
        assert f"Timeline not exported: {says}" in capsys.readouterr().out
