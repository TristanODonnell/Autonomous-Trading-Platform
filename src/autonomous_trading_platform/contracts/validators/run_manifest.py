# autonomous_trading_platform/contracts/validators/run_manifest.py

from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest

from .core import Rule, is_positive

RUN_MANIFEST_RULES: list[Rule[RunManifest]] = [
    Rule(
        code="CAPITAL_BUCKET_POSITIVE",
        field="capital_bucket",
        check=lambda rm, _ctx: is_positive(rm.capital_bucket),
        message=lambda rm, _ctx: "capital_bucket must be > 0",
    ),
    Rule(
        code="BACKTEST_REQUIRES_START_AND_END_DATE",
        field=None,
        check=lambda rm, _ctx: (
            rm.run_type != RunType.BACKTEST
            or (rm.start_date is not None and rm.end_date is not None)
        ),
        message=lambda rm, _ctx: "backtest runs must define start_date and end_date",
    ),
    Rule(
        code="BACKTEST_REQUIRES_RANDOM_SEED",
        field="random_seed",
        check=lambda rm, _ctx: rm.run_type != RunType.BACKTEST or rm.random_seed is not None,
        message=lambda rm, _ctx: "backtest runs must define random_seed",
    ),
    Rule(
        code="BACKTEST_REQUIRES_COST_AND_FILL_MODEL",
        field=None,
        check=lambda rm, _ctx: (
            rm.run_type != RunType.BACKTEST
            or (rm.cost_model is not None and rm.fill_model is not None)
        ),
        message=lambda rm, _ctx: "backtest runs must define cost_model and fill_model",
    ),
    Rule(
        code="DATASET_AND_UNIVERSE_VERSION_REQUIRED",
        field=None,
        check=lambda rm, _ctx: rm.dataset_version is not None and rm.universe_version is not None,
        message=lambda rm, _ctx: "dataset_version and universe_version must be defined",
    ),
]
