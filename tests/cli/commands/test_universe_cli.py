from __future__ import annotations

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
