import argparse
from pprint import pprint

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.services.kill_switch_service import (
    KillSwitchService,
)
from autonomous_trading_platform.safety.services.live_trading_gate_service import (
    LiveTradingGateService,
)
from autonomous_trading_platform.safety.services.runtime_gate_service import (
    RuntimeGateService,
)


def build_services() -> tuple[
    RuntimeGateService,
    KillSwitchService,
    LiveTradingGateService,
]:
    settings = Settings()
    environment_policy = EnvironmentSafetyPolicy(settings)
    runtime_gate_service = RuntimeGateService()
    kill_switch_service = KillSwitchService()
    live_gate_service = LiveTradingGateService(
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
    )
    return runtime_gate_service, kill_switch_service, live_gate_service


def main() -> None:
    parser = argparse.ArgumentParser(description="Safety gate CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    arm_parser = subparsers.add_parser("arm-live")
    arm_parser.add_argument("--reason", required=True)
    arm_parser.add_argument("--armed-by", required=True)

    subparsers.add_parser("disarm-live")

    enable_parser = subparsers.add_parser("enable-kill-switch")
    enable_parser.add_argument("--reason", required=True)
    enable_parser.add_argument("--updated-by", required=True)

    disable_parser = subparsers.add_parser("disable-kill-switch")
    disable_parser.add_argument("--reason", required=True)
    disable_parser.add_argument("--updated-by", required=True)

    status_parser = subparsers.add_parser("gate-status")
    status_parser.add_argument("--account-id", required=True)

    args = parser.parse_args()

    runtime_gate_service, kill_switch_service, live_gate_service = build_services()

    if args.command == "arm-live":
        runtime_gate_service.arm(reason=args.reason, armed_by=args.armed_by)
        pprint(runtime_gate_service.get_status())
        return
    # example call
    # python -m autonomous_trading_platform.cli.safety_cli arm-live --reason "manual testing" --armed-by "tristan"

    if args.command == "disarm-live":
        runtime_gate_service.disarm()
        pprint(runtime_gate_service.get_status())
        return
    # example call
    # python -m autonomous_trading_platform.cli.safety_cli disarm-live --reason "manual testing" --armed-by "tristan"

    if args.command == "enable-kill-switch":
        kill_switch_service.enable(reason=args.reason, updated_by=args.updated_by)
        pprint(kill_switch_service.get_status())
        return
    # example call
    # python -m autonomous_trading_platform.cli.safety_cli enable-kill-switch --reason "manual testing" --armed-by "tristan"

    if args.command == "disable-kill-switch":
        kill_switch_service.disable(reason=args.reason, updated_by=args.updated_by)
        pprint(kill_switch_service.get_status())
        return
    # example call
    # python -m autonomous_trading_platform.cli.safety_cli disable-kill-switch --reason "manual testing" --armed-by "tristan"

    if args.command == "gate-status":
        pprint(live_gate_service.get_gate_status(account_id=args.account_id))
        return
    # python -m autonomous_trading_platform.cli.safety_cli gate-status --account_id "test_id"


if __name__ == "__main__":
    main()
