import random
from datetime import date
from uuid import uuid4

import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
)


class DummyRepo:
    def __init__(self):
        self._store = {}
        self.session = self  # mimic sqlalchemy session

    def commit(self):
        pass

    def flush(self):
        pass


class DummyManifestRepo(DummyRepo):
    def upsert(self, manifest):
        self._store[manifest.run_id] = manifest
        return manifest

    def get_by_run_id(self, run_id):
        return self._store.get(run_id)

    def to_contract(self, row):
        return row


class DummyMetricsSummaryRepo(DummyRepo):
    def upsert(self, row):
        self._store[row.metrics_snapshot_id] = row
        return row

    def to_row(self, contract):
        return contract


@pytest.fixture
def runner():
    return SimulationRunner(
        dataset_resolver=None,
        window_loader=None,
        result_recorder=None,
        execution_engine=None,
        context_builder=None,
        simulated_execution_service=None,
        simulation_run_repository=None,
        strategy_config_repository=None,
        experiment_repository=None,
        manifest_service=DummyManifestRepo(),
        metrics_summary_repository=DummyMetricsSummaryRepo(),
        strategy_factory=None,
    )


@pytest.fixture
def valid_request():
    return SimulationRunRequest(
        strategy_id="test_strategy",
        strategy_config={"type": "stub", "parameters": {}},
        dataset_version="test_dataset",
        random_seed=42,
        price_basis=PriceBasis.ADJUSTED,
        symbols=["AAPL"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2),
    )


def test_run_rejects_non_int_random_seed(runner, valid_request):
    valid_request.random_seed = None

    with pytest.raises(ValueError, match="random_seed must be an int"):
        runner.run(valid_request)


def test_init_rng_produces_isolated_deterministic_rng(runner):
    """_init_rng must return an isolated random.Random seeded from the given int."""
    rng_a = runner._init_rng(42)
    rng_b = runner._init_rng(42)

    # Same seed → same sequence from isolated instances.
    assert rng_a.random() == rng_b.random()
    assert rng_a.random() == rng_b.random()


def test_init_rng_does_not_mutate_global_random_state(runner):
    """_init_rng must not call random.seed() or touch the global random module."""
    before = random.getstate()
    runner._init_rng(42)
    after = random.getstate()

    assert before == after, "_init_rng must not mutate the global random module state"


def test_init_rng_different_seeds_produce_different_sequences(runner):
    rng_a = runner._init_rng(1)
    rng_b = runner._init_rng(2)

    values_a = [rng_a.random() for _ in range(5)]
    values_b = [rng_b.random() for _ in range(5)]

    assert values_a != values_b


def test_record_run_started_writes_random_seed_to_manifest(runner, valid_request):
    run_id = uuid4()

    runner._record_run_started(
        run_id=run_id,
        request=valid_request,
        experiment_id="experiment_test",
        resolved_dataset_metadata={},
        resolved_feature_dataset_ids={},
    )

    manifest = runner.manifest_service.get_by_run_id(run_id)

    assert manifest is not None
    assert manifest.random_seed == valid_request.random_seed

    assert manifest.schema_definition is not None
    det = manifest.schema_definition["determinism"]
    assert det["random_seed"] == valid_request.random_seed
    assert det["isolated_python_rng"] is True
    assert det["deterministic_run_id"] is True
    assert det["deterministic_fill_ids"] is True
