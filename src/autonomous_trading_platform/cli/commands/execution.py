from __future__ import annotations

import argparse

from autonomous_trading_platform.cli.formatters import print_header, print_json


def register(subparsers) -> None:
    execution_parser = subparsers.add_parser("execution", help="Execution operations")
    execution_subparsers = execution_parser.add_subparsers(
        dest="execution_command",
        required=True,
    )

    reconcile_order_parser = execution_subparsers.add_parser(
        "reconcile-order",
        help="Reconcile a single order",
    )
    reconcile_order_parser.add_argument("--order-id", required=True)
    reconcile_order_parser.set_defaults(func=handle_reconcile_order)

    reconcile_open_orders_parser = execution_subparsers.add_parser(
        "reconcile-open-orders",
        help="Reconcile all open orders",
    )
    reconcile_open_orders_parser.set_defaults(func=handle_reconcile_open_orders)

    inspect_order_parser = execution_subparsers.add_parser(
        "inspect-order",
        help="Inspect order",
    )
    inspect_order_parser.add_argument("--order-id", required=True)
    inspect_order_parser.set_defaults(func=handle_inspect_order)

    inspect_position_parser = execution_subparsers.add_parser(
        "inspect-position",
        help="Inspect position",
    )
    inspect_position_parser.add_argument("--symbol", required=True)
    inspect_position_parser.set_defaults(func=handle_inspect_position)

    inspect_cash_parser = execution_subparsers.add_parser(
        "inspect-cash",
        help="Inspect cash snapshot",
    )
    inspect_cash_parser.set_defaults(func=handle_inspect_cash)


def handle_reconcile_order(args: argparse.Namespace) -> int:
    print_header("Reconcile Order")
    print_json({"order_id": args.order_id, "status": "not_implemented"})
    return 0


def handle_reconcile_open_orders(args: argparse.Namespace) -> int:
    print_header("Reconcile Open Orders")
    print_json({"status": "not_implemented"})
    return 0


def handle_inspect_order(args: argparse.Namespace) -> int:
    print_header("Inspect Order")
    print_json({"order_id": args.order_id, "status": "not_implemented"})
    return 0


def handle_inspect_position(args: argparse.Namespace) -> int:
    print_header("Inspect Position")
    print_json({"symbol": args.symbol, "status": "not_implemented"})
    return 0


def handle_inspect_cash(args: argparse.Namespace) -> int:
    print_header("Inspect Cash")
    print_json({"status": "not_implemented"})
    return 0
