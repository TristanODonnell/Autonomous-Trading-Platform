from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from autonomous_trading_platform.cli.commands import settings as settings_cmd
from autonomous_trading_platform.cli.main import build_parser


class TestSettingsParserCoverage:
    def test_core_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["settings", "show", "--json"])
        assert args.func is settings_cmd.handle_show
        assert args.json is True

        args = parser.parse_args(["settings", "sources"])
        assert args.func is settings_cmd.handle_sources

        args = parser.parse_args(["settings", "validate", "--config", "settings.yaml"])
        assert args.func is settings_cmd.handle_validate
        assert args.config == Path("settings.yaml")

        args = parser.parse_args(["settings", "diff", "--config", "settings.yaml"])
        assert args.func is settings_cmd.handle_diff

        args = parser.parse_args(["settings", "seed", "--config", "settings.yaml", "--dry-run"])
        assert args.func is settings_cmd.handle_seed
        assert args.dry_run is True

        args = parser.parse_args(
            [
                "settings",
                "set",
                "--key",
                "risk_tolerance",
                "--value",
                "high",
                "--updated-by",
                "ops",
                "--reason",
                "manual tune",
            ]
        )
        assert args.func is settings_cmd.handle_set
        assert args.key == "risk_tolerance"

    def test_read_only_and_export_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["settings", "risk-profile"])
        assert args.func is settings_cmd.handle_risk_profile

        args = parser.parse_args(["settings", "advanced"])
        assert args.func is settings_cmd.handle_advanced

        args = parser.parse_args(["settings", "export", "--output", "out/settings.json"])
        assert args.func is settings_cmd.handle_export
        assert args.output == Path("out/settings.json")

        args = parser.parse_args(["settings", "snapshot", "--output", "out/snapshot.yaml"])
        assert args.func is settings_cmd.handle_snapshot

        args = parser.parse_args(
            [
                "settings",
                "verify-persisted",
                "--expect",
                "risk_tolerance=medium",
                "--expect",
                "auto_rebalance_enabled=false",
            ]
        )
        assert args.func is settings_cmd.handle_verify_persisted
        assert args.expect == ["risk_tolerance=medium", "auto_rebalance_enabled=false"]

        args = parser.parse_args(["settings", "audit-log", "--limit", "5"])
        assert args.func is settings_cmd.handle_audit_log
        assert args.limit == 5

    def test_cross_domain_wrappers_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "settings",
                "verify-runtime-effect",
                "--controls",
                "controls.yaml",
                "--config",
                "settings.yaml",
                "--symbols",
                "SPY,QQQ",
                "--start",
                "2026-01-01",
                "--end",
                "2026-01-31",
                "--parameter",
                "risk_tolerance",
                "--print-summary",
            ]
        )
        assert args.func is settings_cmd.handle_verify_runtime_effect
        assert args.config == Path("settings.yaml")
        assert args.parameters == ["risk_tolerance"]
        assert args.print_summary is True

        args = parser.parse_args(
            [
                "settings",
                "verify-notifications",
                "--controls",
                "controls.yaml",
                "--config",
                "settings.yaml",
            ]
        )
        assert args.func is settings_cmd.handle_verify_notifications

    def test_reset_defaults_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["settings", "reset-defaults", "--dry-run"])
        assert args.func is settings_cmd.handle_reset_defaults
        assert args.dry_run is True


class TestSettingsValidation:
    def test_unknown_key_rejected(self) -> None:
        assert settings_cmd._validate_patch({"not_a_setting": "x"}) is None

    def test_range_validation_rejected(self) -> None:
        assert settings_cmd._validate_patch({"max_drawdown_limit": "1.5"}) is None

    def test_valid_patch_is_normalized(self) -> None:
        patch = settings_cmd._validate_patch(
            {
                "risk_tolerance": "high",
                "max_drawdown_limit": "0.15",
                "auto_rebalance_enabled": True,
            }
        )

        assert patch == {
            "risk_tolerance": "high",
            "max_drawdown_limit": Decimal("0.15"),
            "auto_rebalance_enabled": True,
        }

    def test_value_coercion(self) -> None:
        assert settings_cmd._coerce_value("auto_rebalance_enabled", "yes") is True
        assert settings_cmd._coerce_value("min_paper_trading_period_days", "45") == 45
        assert settings_cmd._coerce_value("max_drawdown_limit", "0.2") == Decimal("0.2")
