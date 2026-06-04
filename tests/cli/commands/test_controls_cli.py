from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from autonomous_trading_platform.cli.commands import controls as controls_cmd
from autonomous_trading_platform.cli.main import build_parser


class TestControlsParserCoverage:
    def test_global_control_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["controls", "state", "--json"])
        assert args.func is controls_cmd.handle_state
        assert args.json is True

        args = parser.parse_args(
            [
                "controls",
                "pause",
                "--updated-by",
                "ops",
                "--reason",
                "market data incident",
            ]
        )
        assert args.func is controls_cmd.handle_pause

        args = parser.parse_args(
            [
                "controls",
                "resume",
                "--updated-by",
                "ops",
                "--reason",
                "market data recovered",
            ]
        )
        assert args.func is controls_cmd.handle_resume

        args = parser.parse_args(["controls", "mode", "show"])
        assert args.func is controls_cmd.handle_mode_show

        args = parser.parse_args(
            [
                "controls",
                "mode",
                "set",
                "--mode",
                "simulation",
                "--updated-by",
                "ops",
                "--reason",
                "research run",
            ]
        )
        assert args.func is controls_cmd.handle_mode_set
        assert args.mode == "simulation"

    def test_strategy_and_allocation_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["controls", "strategy", "list", "--json"])
        assert args.func is controls_cmd.handle_strategy_list

        args = parser.parse_args(
            [
                "controls",
                "strategy",
                "disable",
                "--strategy-id",
                "momentum_v1",
                "--updated-by",
                "ops",
                "--reason",
                "operator pause",
            ]
        )
        assert args.func is controls_cmd.handle_strategy_disable

        args = parser.parse_args(
            [
                "controls",
                "strategy",
                "enable",
                "--strategy-id",
                "momentum_v1",
                "--updated-by",
                "ops",
                "--reason",
                "operator resume",
            ]
        )
        assert args.func is controls_cmd.handle_strategy_enable

        args = parser.parse_args(["controls", "allocation", "list", "--json"])
        assert args.func is controls_cmd.handle_allocation_list

        args = parser.parse_args(["controls", "allocation", "status"])
        assert args.func is controls_cmd.handle_allocation_status

        args = parser.parse_args(
            [
                "controls",
                "allocation",
                "preview",
                "--strategy-id",
                "momentum_v1",
                "--allocation-pct",
                "25",
            ]
        )
        assert args.func is controls_cmd.handle_allocation_preview
        assert args.allocation_pct == Decimal("25")

        args = parser.parse_args(
            [
                "controls",
                "allocation",
                "set",
                "--strategy-id",
                "momentum_v1",
                "--allocation-pct",
                "25",
                "--updated-by",
                "risk",
                "--reason",
                "rebalance",
                "--dry-run",
            ]
        )
        assert args.func is controls_cmd.handle_allocation_set
        assert args.dry_run is True

        args = parser.parse_args(
            [
                "controls",
                "allocation",
                "clear",
                "--strategy-id",
                "momentum_v1",
                "--updated-by",
                "risk",
                "--reason",
                "remove override",
                "--dry-run",
            ]
        )
        assert args.func is controls_cmd.handle_allocation_clear

    def test_fixture_audit_export_and_verify_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["controls", "validate", "--config", "controls.yaml"])
        assert args.func is controls_cmd.handle_validate
        assert args.config == Path("controls.yaml")

        args = parser.parse_args(["controls", "diff", "--config", "controls.yaml"])
        assert args.func is controls_cmd.handle_diff

        args = parser.parse_args(
            [
                "controls",
                "seed",
                "--config",
                "controls.yaml",
                "--updated-by",
                "ops",
                "--reason",
                "seed controls",
                "--dry-run",
            ]
        )
        assert args.func is controls_cmd.handle_seed
        assert args.dry_run is True

        args = parser.parse_args(["controls", "audit-log", "--limit", "5"])
        assert args.func is controls_cmd.handle_audit_log
        assert args.limit == 5

        args = parser.parse_args(["controls", "export", "--output", "out/controls.json"])
        assert args.func is controls_cmd.handle_export

        args = parser.parse_args(
            [
                "controls",
                "verify-runtime-gates",
                "--strategy-id",
                "momentum_v1",
                "--mode",
                "paper",
            ]
        )
        assert args.func is controls_cmd.handle_verify_runtime_gates


class TestControlsFixtureValidation:
    def test_kill_switch_rejected_from_controls_fixture(self, tmp_path: Path) -> None:
        path = tmp_path / "controls.yaml"
        path.write_text("controls:\n  kill_switch_enabled: true\n", encoding="utf-8")

        assert controls_cmd._read_controls_fixture(path) is None

    def test_controls_fixture_normalized(self, tmp_path: Path) -> None:
        path = tmp_path / "controls.yaml"
        path.write_text(
            "\n".join(
                [
                    "controls:",
                    "  trading_paused: true",
                    "  trading_mode: simulation",
                    "strategies:",
                    "  - strategy_id: momentum_v1",
                    "    enabled: false",
                    "allocations:",
                    "  - strategy_id: momentum_v1",
                    "    allocation_pct: 25",
                ]
            ),
            encoding="utf-8",
        )

        fixture = controls_cmd._read_controls_fixture(path)

        assert fixture == {
            "controls": {"trading_paused": True, "trading_mode": "simulation"},
            "strategies": [{"strategy_id": "momentum_v1", "enabled": False}],
            "allocations": [{"strategy_id": "momentum_v1", "allocation_pct": Decimal("25")}],
        }

    def test_live_mode_requires_confirmation_in_handler(self) -> None:
        args = type(
            "Args",
            (),
            {
                "mode": "live",
                "confirm_live": False,
                "updated_by": "ops",
                "reason": "test",
            },
        )()

        assert controls_cmd.handle_mode_set(args) == 1
