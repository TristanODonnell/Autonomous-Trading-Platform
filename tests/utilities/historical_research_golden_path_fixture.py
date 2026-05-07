from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from tests.utilities.corporate_action_ingestion_cycle_fixture import (
    _patch_runtime_seams as _patch_corporate_action_runtime_seams,
)
from tests.utilities.experiment_pipeline_cycle_fixture import (
    EXPERIMENT_DATASET_VERSION,
    EXPERIMENT_END_DATE,
    EXPERIMENT_START_DATE,
    EXPERIMENT_SYMBOLS,
    FakeExperimentOrchestrationService,
)
from tests.utilities.experiment_pipeline_cycle_fixture import (
    _patch_runtime_seams as _patch_experiment_pipeline_runtime_seams,
)
from tests.utilities.feature_pipeline_cycle_fixture import (
    _patch_runtime_seams as _patch_feature_pipeline_runtime_seams,
)
from tests.utilities.market_backfill_cycle_fixture import (
    BACKFILL_END_UTC,
    BACKFILL_NOW_UTC,
    BACKFILL_START_UTC,
    BACKFILL_SYMBOLS,
)
from tests.utilities.market_backfill_cycle_fixture import (
    _patch_runtime_seams as _patch_backfill_runtime_seams,
)


@dataclass(frozen=True)
class SeededHistoricalResearchGoldenPathFixture:
    now_utc: datetime
    start: datetime
    end: datetime
    symbols: list[str]
    symbol_count: int
    data_root: Path
    experiment_plan: ExperimentDefinition
    fake_orchestration_service: FakeExperimentOrchestrationService


def seed_historical_research_golden_path_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededHistoricalResearchGoldenPathFixture:
    _patch_backfill_runtime_seams(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
    )
    _patch_corporate_action_runtime_seams(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
    )
    _patch_feature_pipeline_runtime_seams(
        session=session,
        monkeypatch=monkeypatch,
        data_root=data_root,
    )

    fake_service = FakeExperimentOrchestrationService()

    _patch_experiment_pipeline_runtime_seams(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
        fake_service=fake_service,
    )

    experiment_plan = ExperimentDefinition(
        experiment_id="golden_path_test_experiment",
        experiment_type=ExperimentType.SWEEP,
        description="Experiment step in the historical research golden path",
        strategy_set=[{"type": "moving_average_crossover"}],
        parameter_grid=[],
        parameter_space={"short_window": [5], "long_window": [20]},
        dataset_version=EXPERIMENT_DATASET_VERSION,
        universe_version="v1",
        price_basis=PriceBasis.RAW,
        symbols=EXPERIMENT_SYMBOLS,
        start_date=EXPERIMENT_START_DATE,
        end_date=EXPERIMENT_END_DATE,
        random_seed=42,
        initial_cash=100_000.0,
        staged_pipeline_config=None,
    )

    return SeededHistoricalResearchGoldenPathFixture(
        now_utc=BACKFILL_NOW_UTC,
        start=BACKFILL_START_UTC,
        end=BACKFILL_END_UTC,
        symbols=BACKFILL_SYMBOLS,
        symbol_count=len(BACKFILL_SYMBOLS),
        data_root=data_root,
        experiment_plan=experiment_plan,
        fake_orchestration_service=fake_service,
    )
