from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.research.pipeline.pipeline_runner import (
    PipelineRunResult,
    StagedPipelineConfig,
)
from autonomous_trading_platform.research.pipeline.stages.base_stage import BaseStage, StageResult
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

EXPERIMENT_NOW_UTC = datetime(2025, 3, 31, 21, 0, tzinfo=UTC)
EXPERIMENT_DATASET_VERSION = "exp_pipeline_fixture_raw_v1"
EXPERIMENT_SYMBOLS = ["AAPL", "SPY"]
EXPERIMENT_START_DATE = date(2025, 1, 2)
EXPERIMENT_END_DATE = date(2025, 1, 3)


@dataclass(frozen=True)
class SeededExperimentPipelineCycleFixture:
    now_utc: datetime
    experiment_plan: ExperimentDefinition
    staged_experiment_plan: ExperimentDefinition
    data_root: Path
    fake_orchestration_service: FakeExperimentOrchestrationService


class FakeStage(BaseStage):
    """Minimal stage for constructing a StagedPipelineConfig in tests."""

    @property
    def stage_name(self) -> str:
        return "fake_stage"

    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
    ) -> StageResult:
        return StageResult(stage_name=self.stage_name, survivors=survivors)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], simulation_runner: SimulationRunner) -> FakeStage:
        return cls()


class FakeExperimentOrchestrationService:
    """
    Replaces ExperimentOrchestrationService in cycle tests.

    Records which dispatch method was called and with what plan, so tests
    can assert on routing behaviour without running any simulation logic.
    """

    def __init__(self) -> None:
        self.run_experiment_calls: list[ExperimentDefinition] = []
        self.run_staged_experiment_calls: list[ExperimentDefinition] = []
        self._should_raise: Exception | None = None

    def configure_to_raise(self, exc: Exception) -> None:
        self._should_raise = exc

    def run_experiment(self, plan: ExperimentDefinition) -> tuple[list, list]:
        if self._should_raise is not None:
            raise self._should_raise
        self.run_experiment_calls.append(plan)
        return [], []

    def run_staged_experiment(self, plan: ExperimentDefinition) -> PipelineRunResult:
        if self._should_raise is not None:
            raise self._should_raise
        self.run_staged_experiment_calls.append(plan)
        return PipelineRunResult()


class FakeSimulationContext:
    """
    Minimal SimulationContext stand-in.

    The cycle only accesses experiment_orchestration_service (and
    simulation_runner when loading from YAML). Both are provided here.
    """

    def __init__(self, fake_service: FakeExperimentOrchestrationService) -> None:
        self.experiment_orchestration_service = fake_service
        self.simulation_runner: SimulationRunner | None = None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def seed_experiment_pipeline_cycle_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededExperimentPipelineCycleFixture:
    _seed_environment(monkeypatch)

    fake_service = FakeExperimentOrchestrationService()

    _patch_runtime_seams(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
        fake_service=fake_service,
    )

    experiment_plan = ExperimentDefinition(
        experiment_id="test_experiment_non_staged",
        experiment_type=ExperimentType.SWEEP,
        description="Fixture non-staged experiment for cycle testing",
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

    staged_experiment_plan = ExperimentDefinition(
        experiment_id="test_experiment_staged",
        experiment_type=ExperimentType.SWEEP,
        description="Fixture staged experiment for cycle testing",
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
        staged_pipeline_config=StagedPipelineConfig(stages=[FakeStage()]),
    )

    return SeededExperimentPipelineCycleFixture(
        now_utc=EXPERIMENT_NOW_UTC,
        experiment_plan=experiment_plan,
        staged_experiment_plan=staged_experiment_plan,
        data_root=data_root,
        fake_orchestration_service=fake_service,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _seed_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")


def _patch_runtime_seams(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
    fake_service: FakeExperimentOrchestrationService,
) -> None:
    import autonomous_trading_platform.scheduler.cycles.run_experiment_pipeline_cycle as cycle_module
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    monkeypatch.setattr(cycle_module, "get_session", lambda: session)

    fake_context = FakeSimulationContext(fake_service)
    monkeypatch.setattr(
        cycle_module,
        "build_simulation_context",
        lambda **_kwargs: fake_context,
    )

    monkeypatch.setattr(
        parquet_paths,
        "get_data_root",
        lambda: data_root,
        raising=False,
    )

    try:
        from autonomous_trading_platform.config.settings import Settings

        monkeypatch.setattr(Settings, "DATA_ROOT", str(data_root), raising=False)
    except Exception:
        pass

    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("PARQUET_DATA_ROOT", str(data_root))
    monkeypatch.setenv("LOCAL_DATA_ROOT", str(data_root))


# ---------------------------------------------------------------------------
# Shared YAML fixture content
# ---------------------------------------------------------------------------

EXPERIMENT_YAML_CONTENT = f"""\
experiment_id: test_yaml_experiment
dataset_version: {EXPERIMENT_DATASET_VERSION}
price_basis: raw
start_date: "{EXPERIMENT_START_DATE.isoformat()}"
end_date: "{EXPERIMENT_END_DATE.isoformat()}"
strategy_set:
  - type: moving_average_crossover
parameter_space:
  short_window: [5]
  long_window: [20]
"""
