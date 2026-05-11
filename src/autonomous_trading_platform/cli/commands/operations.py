from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_soak_verification_service import (
    RuntimeSoakVerificationService,
)
from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakStatus,
)
from autonomous_trading_platform.db import get_session


@dataclass
class OperationsCliDependencies:
    session: Session
    settings: Settings


def build_dependencies() -> OperationsCliDependencies:
    return OperationsCliDependencies(
        session=get_session(),
        settings=Settings(),
    )


def register(subparsers) -> None:
    operations_parser = subparsers.add_parser("operations", help="Operations commands")
    operations_subparsers = operations_parser.add_subparsers(
        dest="operations_command",
        required=True,
    )

    verify_runtime_soak_parser = operations_subparsers.add_parser(
        "verify-runtime-soak",
        help="Run runtime soak verification checks",
    )
    verify_runtime_soak_parser.add_argument("--window-start", required=True)
    verify_runtime_soak_parser.add_argument("--window-end", required=True)
    verify_runtime_soak_parser.add_argument("--stale-after-minutes", type=int, default=15)
    verify_runtime_soak_parser.set_defaults(func=handle_verify_runtime_soak)


def handle_verify_runtime_soak(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    window_start = parse_datetime(args.window_start)
    window_end = parse_datetime(args.window_end)

    if window_end < window_start:
        raise ValueError("--window-end must be greater than or equal to --window-start")

    service = RuntimeSoakVerificationService(
        session=deps.session,
        environment=deps.settings.trading_environment.value,
        stale_after=timedelta(minutes=args.stale_after_minutes),
    )

    try:
        report = service.verify(
            window_start=window_start,
            window_end=window_end,
        )

        print_header("Verify Runtime Soak")
        print_json(report.model_dump(mode="json"))

        return 1 if report.status == RuntimeSoakStatus.FAILED else 0
    finally:
        deps.session.close()
