from __future__ import annotations

from autonomous_trading_platform.cli.commands import controls as controls_cmd
from autonomous_trading_platform.cli.commands import settings as settings_cmd
from autonomous_trading_platform.cli.main import build_parser


def test_controls_automation_registered() -> None:
    args = build_parser().parse_args(["controls", "automation"])

    assert args.func is controls_cmd.handle_automation


def test_automation_switches_are_settable_without_a_deploy() -> None:
    """Plan 3.1: every per-automation switch is a settings key `atp settings set` accepts."""
    parser = build_parser()
    for key, value in (
        ("auto_promote_enabled", "false"),
        ("auto_demote_on_breach", "false"),
        ("auto_rebalance_enabled", "false"),
        ("portfolio_review_mode", "advisory"),
        ("worker_results_enabled", "true"),
        ("worker_result_max_age_days", "5"),
        ("worker_result_require_matching_git_sha", "false"),
        ("min_rebalance_interval_hours", "168"),
    ):
        args = parser.parse_args(
            [
                "settings",
                "set",
                "--key",
                key,
                "--value",
                value,
                "--updated-by",
                "ops",
                "--reason",
                "x",
            ]
        )
        assert args.func is settings_cmd.handle_set
        assert args.key == key

    assert settings_cmd._coerce_value("worker_results_enabled", "true") is True
    assert settings_cmd._coerce_value("worker_result_max_age_days", "5") == 5
    assert settings_cmd._coerce_value("portfolio_review_mode", "auto") == "auto"
