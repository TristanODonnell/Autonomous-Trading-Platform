from datetime import UTC, date, datetime
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
    RunType,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.run_manifest import RUN_MANIFEST_RULES


def make_run_manifest(**overrides) -> RunManifest:
    data = {
        "run_id": uuid4(),
        "run_type": RunType.LIVE,
        "created_at": datetime.now(UTC),
        "environment": "dev",
        "broker": "alpaca",
        "broker_account_id": "acct_123",
        "strategy_id": "strategy_001",
        "strategy_version": "1.0.0",
        "strategy_config": {},
        "capital_bucket": 10000.0,
        "interval": BarInterval.FIVE_MIN,
        "start_date": date.today(),
        "end_date": None,
        "dataset_version": "dataset_v1",
        "price_basis": PriceBasis.RAW,
        "universe_version": "universe_v1",
        "cost_model": None,
        "fill_model": None,
        "random_seed": 42,
        "git_commit": "abcdef123",
        "docker_image": None,
        "python_version": None,
        "dependency_lock_hash": None,
        "notes": None,
    }

    data.update(overrides)
    return RunManifest(**data)


def test_valid_run_manifest_passes():
    manifest = make_run_manifest()

    result = run_rules(manifest, RUN_MANIFEST_RULES)

    assert result.ok
    assert result.violations == []


def test_capital_bucket_must_be_positive():
    manifest = make_run_manifest(capital_bucket=0)

    result = run_rules(manifest, RUN_MANIFEST_RULES)

    assert not result.ok
    assert any(v.code == "CAPITAL_BUCKET_POSITIVE" for v in result.violations)


def test_backtest_requires_start_and_end_date():
    manifest = make_run_manifest(
        run_type=RunType.BACKTEST,
        end_date=None,
    )

    result = run_rules(manifest, RUN_MANIFEST_RULES)

    assert not result.ok
    assert any(v.code == "BACKTEST_REQUIRES_START_AND_END_DATE" for v in result.violations)


def test_backtest_requires_random_seed():
    manifest = make_run_manifest(
        run_type=RunType.BACKTEST,
        random_seed=None,
    )

    result = run_rules(manifest, RUN_MANIFEST_RULES)

    assert not result.ok
    assert any(v.code == "BACKTEST_REQUIRES_RANDOM_SEED" for v in result.violations)


def test_backtest_requires_cost_and_fill_model():
    manifest = make_run_manifest(
        run_type=RunType.BACKTEST,
        cost_model=None,
        fill_model=None,
    )

    result = run_rules(manifest, RUN_MANIFEST_RULES)

    assert not result.ok
    assert any(v.code == "BACKTEST_REQUIRES_COST_AND_FILL_MODEL" for v in result.violations)
