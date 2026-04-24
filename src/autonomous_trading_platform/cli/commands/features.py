from __future__ import annotations

from datetime import date

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.observability.telemetry import setup_telemetry
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)


def _parse_symbols(value: str | None) -> list[str] | None:
    if value is None or value.strip() == "":
        return None

    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def run_features(args) -> int:
    setup_telemetry("cli-feature-pipeline")

    run_feature_pipeline_cycle(
        price_basis=PriceBasis(args.price_basis.lower()),
        dataset_version_id=args.dataset_version_id,
        symbols=_parse_symbols(args.symbols),
        start_date=args.start_date,
        end_date=args.end_date,
        include_returns=args.include_returns,
        include_volatility=args.include_volatility,
        include_moving_average=args.include_moving_average,
        include_liquidity=args.include_liquidity,
        include_regime=args.include_regime,
    )

    return 0


def register(subparsers) -> None:
    parser = subparsers.add_parser("features")
    feature_subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = feature_subparsers.add_parser("run-pipeline")

    run_parser.add_argument(
        "--dataset-version-id",
        required=True,
        help="Existing source dataset_version_id to compute features from.",
    )
    run_parser.add_argument(
        "--price-basis",
        default="RAW",
        choices=["RAW", "ADJUSTED"],
    )
    run_parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols, e.g. AAPL,MSFT,SPY.",
    )
    run_parser.add_argument("--start-date", type=date.fromisoformat, default=None)
    run_parser.add_argument("--end-date", type=date.fromisoformat, default=None)

    run_parser.add_argument("--include-returns", action="store_true", default=True)
    run_parser.add_argument("--include-volatility", action="store_true", default=True)
    run_parser.add_argument("--include-moving-average", action="store_true", default=True)
    run_parser.add_argument("--include-liquidity", action="store_true", default=True)
    run_parser.add_argument("--include-regime", action="store_true", default=True)

    run_parser.set_defaults(func=run_features)
