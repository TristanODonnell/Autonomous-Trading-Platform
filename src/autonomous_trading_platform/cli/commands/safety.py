from __future__ import annotations

import argparse
from dataclasses import dataclass

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.services.kill_switch_service import KillSwitchService
from autonomous_trading_platform.safety.services.live_trading_gate_service import (
    LiveTradingGateService,
)
from autonomous_trading_platform.safety.services.runtime_gate_service import RuntimeGateService
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


def register(subparsers) -> None:
    safety_parser = subparsers.add_parser("safety", help="Safety operations")
    safety_subparsers = safety_parser.add_subparsers(dest="safety_command", required=True)

    arm_parser = safety_subparsers.add_parser("arm-live", help="Arm live trading")
    arm_parser.add_argument("--reason", required=True)
    arm_parser.add_argument("--armed-by", required=True)
    arm_parser.set_defaults(func=handle_arm_live)

    disarm_parser = safety_subparsers.add_parser("disarm-live", help="Disarm live trading")
    disarm_parser.set_defaults(func=handle_disarm_live)

    enable_parser = safety_subparsers.add_parser(
        "enable-kill-switch",
        help="Enable kill switch",
    )
    enable_parser.add_argument("--reason", required=True)
    enable_parser.add_argument("--updated-by", required=True)
    enable_parser.set_defaults(func=handle_enable_kill_switch)

    disable_parser = safety_subparsers.add_parser(
        "disable-kill-switch",
        help="Disable kill switch",
    )
    disable_parser.add_argument("--reason", required=True)
    disable_parser.add_argument("--updated-by", required=True)
    disable_parser.set_defaults(func=handle_disable_kill_switch)

    gate_status_parser = safety_subparsers.add_parser(
        "gate-status",
        help="Get live trading gate status",
    )
    gate_status_parser.add_argument("--account-id", required=True)
    gate_status_parser.set_defaults(func=handle_gate_status)


@dataclass
class SafetyDependencies:
    runtime_gate_service: RuntimeGateService
    kill_switch_service: KillSwitchService
    live_gate_service: LiveTradingGateService


def build_dependencies() -> SafetyDependencies:
    settings = Settings()
    environment_policy = EnvironmentSafetyPolicy(settings)
    session = get_session()

    runtime_gate_service = RuntimeGateService()
    kill_switch_service = KillSwitchService(
        repository=KillSwitchStateRepository(session=session),
        runtime_control_repo=RuntimeControlStateRepository(session=session),
    )
    live_gate_service = LiveTradingGateService(
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
    )

    return SafetyDependencies(
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
        live_gate_service=live_gate_service,
    )


def handle_gate_status(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    print_header("Gate Status")
    print_json(deps.live_gate_service.get_gate_status(account_id=args.account_id))
    return 0


def handle_arm_live(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    deps.runtime_gate_service.arm(reason=args.reason, armed_by=args.armed_by)
    print_header("Arm Live")
    print_json(deps.runtime_gate_service.get_status())
    return 0


def handle_disarm_live(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    deps.runtime_gate_service.disarm()
    print_header("Disarm Live")
    print_json(deps.runtime_gate_service.get_status())
    return 0


def handle_enable_kill_switch(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    deps.kill_switch_service.enable(reason=args.reason, updated_by=args.updated_by)
    print_header("Enable Kill Switch")
    print_json(deps.kill_switch_service.get_status())
    return 0


def handle_disable_kill_switch(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    deps.kill_switch_service.disable(reason=args.reason, updated_by=args.updated_by)
    print_header("Disable Kill Switch")
    print_json(deps.kill_switch_service.get_status())
    return 0
