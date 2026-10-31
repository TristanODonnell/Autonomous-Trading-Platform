from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from autonomous_trading_platform.cli.commands import universe as universe_cmd
from autonomous_trading_platform.cli.main import build_parser


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
        assert args.dry_run and not args.enforce and not args.force

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
