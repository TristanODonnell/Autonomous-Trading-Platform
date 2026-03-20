from __future__ import annotations

import argparse
from collections.abc import Callable

from autonomous_trading_platform.cli.formatters import format_exception, print_error

Handler = Callable[[argparse.Namespace], int]


def run_handler(handler: Handler, args: argparse.Namespace) -> int:
    try:
        return handler(args)
    except Exception as exc:
        print_error(str(exc))
        print(format_exception(exc))
        return 1
