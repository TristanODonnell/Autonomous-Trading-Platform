from __future__ import annotations

import platform
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any

import yaml
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    CycleMetricSet,
    StepMetricSet,
    record_cycle_completed,
    record_cycle_failed,
    record_cycle_started,
    record_step_completed,
    record_step_failed,
    record_step_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    experiment_pipeline_cycle_duration,
    experiment_pipeline_cycle_failures,
    experiment_pipeline_cycle_runs,
    experiment_pipeline_cycle_step_duration,
    experiment_pipeline_cycle_step_runs,
)
from autonomous_trading_platform.observability.telemetry import setup_telemetry
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
    build_simulation_context,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.sor.repositories.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

logger = get_logger(__name__)

EXPERIMENT_PIPELINE_CYCLE_METRICS = CycleMetricSet(
    runs=experiment_pipeline_cycle_runs,
    failures=experiment_pipeline_cycle_failures,
    duration=experiment_pipeline_cycle_duration,
)
EXPERIMENT_PIPELINE_STEP_METRICS = StepMetricSet(
    runs=experiment_pipeline_cycle_step_runs,
    duration=experiment_pipeline_cycle_step_duration,
)


def run_experiment_pipeline_cycle(
    *,
    experiment_plan: ExperimentDefinition | None = None,
    config_path: str | None = None,
    now_utc: datetime | None = None,
) -> None:
    """
    Entry point for the experiment pipeline Airflow DAG.

    Accepts either a pre-built ExperimentDefinition or a path to a YAML config
    file (see experiments/staged/ for examples). Exactly one must be provided.
    """
    if experiment_plan is None and config_path is None:
        raise ValueError("Either experiment_plan or config_path must be provided")
    if experiment_plan is not None and config_path is not None:
        raise ValueError("Provide only one of experiment_plan or config_path, not both")

    if now_utc is None:
        now_utc = datetime.now(UTC)
    cycle_wall_start = perf_counter()

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)

    run_id = uuid.uuid4()
    component = "scheduler.run_experiment_pipeline_cycle"
    runtime_job_run_repository = RuntimeJobRunRepository(session)
    job_run_id = str(run_id)
    job_started_at = now_utc

    def _save_runtime_job_run(
        *,
        status: str,
        completed_at: datetime | None,
        error_message: str | None,
        output_summary_json: dict | None,
    ) -> None:
        runtime_job_run_repository.save(
            RuntimeJobRun(
                job_run_id=job_run_id,
                job_name="experiment_pipeline_cycle",
                parent_job_run_id=None,
                status=status,
                trigger_type="scheduler",
                started_at=job_started_at,
                completed_at=completed_at,
                duration_ms=(
                    int((perf_counter() - cycle_wall_start) * 1000)
                    if completed_at is not None
                    else None
                ),
                error_message=error_message,
                correlation_id=str(run_id),
                input_summary_json={
                    "component": component,
                    "config_path": config_path,
                    "experiment_id": experiment_plan.experiment_id if experiment_plan else None,
                },
                output_summary_json=output_summary_json,
            )
        )

    _save_runtime_job_run(
        status="running",
        completed_at=None,
        error_message=None,
        output_summary_json=None,
    )

    base_metadata: dict[str, Any] = {}
    manifest: RunManifest | None = None

    record_cycle_started(
        logger=logger,
        metrics=EXPERIMENT_PIPELINE_CYCLE_METRICS,
        component=component,
        run_id=str(run_id),
    )

    try:
        simulation_context = build_simulation_context(session=session)

        if experiment_plan is None:
            experiment_plan = _load_experiment_definition_from_yaml(
                config_path=config_path,  # type: ignore[arg-type]
                simulation_runner=simulation_context.simulation_runner,
            )

        base_metadata = {
            "experiment_id": experiment_plan.experiment_id,
            "experiment_type": str(experiment_plan.experiment_type),
            "dataset_version": experiment_plan.dataset_version,
            "price_basis": experiment_plan.price_basis.value,
            "symbols": experiment_plan.symbols,
            "config_path": config_path,
        }

        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.BACKTEST,
            created_at=now_utc,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id=experiment_plan.experiment_id,
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal(str(experiment_plan.initial_cash)),
            interval=BarInterval.FIVE_MIN,
            start_date=experiment_plan.start_date,
            end_date=experiment_plan.end_date,
            dataset_version=experiment_plan.dataset_version,
            price_basis=experiment_plan.price_basis,
            universe_version=experiment_plan.universe_version,
            git_commit="dev",
            python_version=platform.python_version(),
            notes=f"Experiment pipeline cycle: {experiment_plan.experiment_id}",
            governance_state=GovernanceState.APPROVED_RESEARCH,
        )
        manifest_service.save(manifest)

        with start_span("experiment_pipeline_cycle.run", timespan=SpanTimespan.CYCLE) as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.experiment_id", experiment_plan.experiment_id)
            cycle_span.set_attribute("ratp.dataset_version", experiment_plan.dataset_version)

            audit_logger.record_run_started(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            step = "run_experiment"
            record_step_started(
                logger=logger,
                metrics=EXPERIMENT_PIPELINE_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()
            try:
                with start_span(
                    "experiment_pipeline_cycle.run_experiment",
                    timespan=SpanTimespan.STEP,
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.experiment_id", experiment_plan.experiment_id)
                    step_span.set_attribute("ratp.step", step)

                    if experiment_plan.staged_pipeline_config is not None:
                        pipeline_result = simulation_context.experiment_orchestration_service.run_staged_experiment(
                            experiment_plan
                        )
                        final_survivors_count = len(pipeline_result.final_survivors)
                        total_simulation_runs = len(pipeline_result.all_simulation_results)
                    else:
                        simulation_results, filter_outputs = (
                            simulation_context.experiment_orchestration_service.run_experiment(
                                experiment_plan
                            )
                        )
                        final_survivors_count = len(
                            [o for o in filter_outputs if o.filter_result.passed]
                        )
                        total_simulation_runs = len(simulation_results)

                step_duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=EXPERIMENT_PIPELINE_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=step_duration,
                )
            except Exception as exc:
                step_duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=EXPERIMENT_PIPELINE_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=step_duration,
                )
                raise

            _save_runtime_job_run(
                status="completed",
                completed_at=datetime.now(UTC),
                error_message=None,
                output_summary_json={
                    "experiment_id": experiment_plan.experiment_id,
                    "final_survivors_count": final_survivors_count,
                    "total_simulation_runs": total_simulation_runs,
                    "last_successful_step": step,
                },
            )

            manifest.status = "completed"
            manifest.current_step = None
            manifest.error_message = None
            manifest_service.save(manifest)

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            total_duration = perf_counter() - cycle_wall_start
            record_cycle_completed(
                logger=logger,
                metrics=EXPERIMENT_PIPELINE_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )

    except Exception as exc:
        total_duration = perf_counter() - cycle_wall_start

        _save_runtime_job_run(
            status="failed",
            completed_at=datetime.now(UTC),
            error_message=str(exc),
            output_summary_json={
                "experiment_id": experiment_plan.experiment_id if experiment_plan else None,
            },
        )

        if manifest is not None:
            manifest.status = "failed"
            manifest.error_message = str(exc)
            manifest_service.save(manifest)

        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        record_cycle_failed(
            logger=logger,
            metrics=EXPERIMENT_PIPELINE_CYCLE_METRICS,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=total_duration,
        )
        raise
    finally:
        session.close()


def _load_experiment_definition_from_yaml(
    *,
    config_path: str,
    simulation_runner: SimulationRunner,
) -> ExperimentDefinition:
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    staged_pipeline_config = None
    pipeline_raw = raw.get("staged_pipeline_config")

    if pipeline_raw:
        stages = [
            StageRegistry.load(stage_raw, simulation_runner) for stage_raw in pipeline_raw["stages"]
        ]
        staged_pipeline_config = StagedPipelineConfig(stages=stages)

    return ExperimentDefinition(
        experiment_id=raw["experiment_id"],
        experiment_type=ExperimentType(raw.get("experiment_type", "sweep")),
        description=raw.get("description"),
        strategy_set=raw.get("strategy_set", []),
        parameter_grid=raw.get("parameter_grid", []),
        parameter_space=raw.get("parameter_space"),
        dataset_version=raw["dataset_version"],
        universe_version=raw.get("universe_version", "v1"),
        price_basis=PriceBasis(raw["price_basis"]),
        symbols=raw.get("symbols", []),
        start_date=date.fromisoformat(raw["start_date"]) if "start_date" in raw else date.today(),
        end_date=date.fromisoformat(raw["end_date"]) if "end_date" in raw else date.today(),
        random_seed=raw.get("random_seed", 42),
        initial_cash=raw.get("initial_cash", 100_000.0),
        staged_pipeline_config=staged_pipeline_config,
    )


if __name__ == "__main__":
    import argparse

    setup_telemetry("scheduler-experiment-pipeline-cycle")

    parser = argparse.ArgumentParser(description="Run the experiment pipeline cycle")
    parser.add_argument("--config", required=True, help="Path to experiment YAML config file")
    args = parser.parse_args()

    run_experiment_pipeline_cycle(config_path=args.config)
