from __future__ import annotations

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
