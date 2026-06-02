from __future__ import annotations

import platform
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.feature_engineering.jobs.liquidity_feature_job import (
    LiquidityFeatureJob,
)
from autonomous_trading_platform.feature_engineering.jobs.moving_average_feature_job import (
    MovingAverageFeatureJob,
)
from autonomous_trading_platform.feature_engineering.jobs.regime_classification_feature_job import (
    RegimeClassificationFeatureJob,
)
from autonomous_trading_platform.feature_engineering.jobs.regime_feature_job import (
    RegimeFeatureJob,
)
from autonomous_trading_platform.feature_engineering.jobs.returns_feature_job import (
    ReturnsFeatureJob,
)
from autonomous_trading_platform.feature_engineering.jobs.volatility_feature_job import (
    VolatilityFeatureJob,
)
from autonomous_trading_platform.feature_engineering.services.feature_dataset_resolver_service import (
    FeatureDatasetResolverService,
)
from autonomous_trading_platform.feature_engineering.services.feature_dataset_writer_service import (
    FeatureDatasetWriterService,
)
from autonomous_trading_platform.feature_engineering.services.feature_pipeline_guard_service import (
    FeaturePipelineGuardService,
)
from autonomous_trading_platform.feature_engineering.services.feature_validation_service import (
    FeatureValidationService,
)
from autonomous_trading_platform.feature_engineering.services.liquidity_feature_service import (
    LiquidityFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.moving_average_feature_service import (
    MovingAverageFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.regime_classification_feature_service import (
    RegimeClassificationFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.regime_feature_service import (
    RegimeFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.returns_feature_service import (
    ReturnsFeatureService,
)
from autonomous_trading_platform.feature_engineering.services.volatility_feature_service import (
    VolatilityFeatureService,
)
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
    feature_pipeline_cycle_duration,
    feature_pipeline_cycle_failures,
    feature_pipeline_cycle_runs,
    feature_pipeline_cycle_step_duration,
    feature_pipeline_cycle_step_runs,
)
from autonomous_trading_platform.observability.telemetry import setup_telemetry
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
    ParquetBarRepository,
)
from autonomous_trading_platform.storage.parquet.repositories.parquet_feature_repository import (
    ParquetFeatureRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.feature_dataset_versions_repository import (
    FeatureDatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.universe_resolution_service import (
    UniverseResolutionService,
)

logger = get_logger(__name__)

FEATURE_PIPELINE_CYCLE_METRICS = CycleMetricSet(
    runs=feature_pipeline_cycle_runs,
    failures=feature_pipeline_cycle_failures,
    duration=feature_pipeline_cycle_duration,
)

FEATURE_PIPELINE_STEP_METRICS = StepMetricSet(
    runs=feature_pipeline_cycle_step_runs,
    duration=feature_pipeline_cycle_step_duration,
)


class MixedLineageError(ValueError):
    pass


def _validate_feature_pipeline_lineage(
    *,
    dataset_version_id: str | None,
    price_basis: PriceBasis,
    dataset_registration_service: DatasetRegistrationService,
) -> None:
    if dataset_version_id is None:
        return

    source_dataset = dataset_registration_service.get_by_dataset_version_id(
        dataset_version_id,
    )

    if source_dataset is None:
        raise MixedLineageError(
            f"Dataset version not found for feature pipeline: {dataset_version_id}"
        )

    if price_basis == PriceBasis.RAW:
        if source_dataset.dataset_name != "raw_bars":
            raise MixedLineageError("RAW feature pipeline must use a raw_bars source dataset.")

        if source_dataset.price_basis != PriceBasis.RAW:
            raise MixedLineageError(
                "RAW feature pipeline source dataset must have RAW price basis."
            )

    if price_basis == PriceBasis.ADJUSTED:
        if source_dataset.dataset_name != "adjusted_bars":
            raise MixedLineageError(
                "ADJUSTED feature pipeline must use an adjusted_bars source dataset."
            )

        if source_dataset.price_basis != PriceBasis.ADJUSTED:
            raise MixedLineageError(
                "ADJUSTED feature pipeline source dataset must have ADJUSTED price basis."
            )

        if source_dataset.source_dataset_version is None:
            raise MixedLineageError(
                "ADJUSTED feature pipeline source dataset must link to a raw source dataset."
            )


def run_feature_pipeline_cycle(
    *,
    now_utc: datetime | None = None,
    price_basis: PriceBasis = PriceBasis.RAW,
    dataset_version_id: str | None = None,
    symbols: list[str] | None = None,
    universe_version_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    include_returns: bool = True,
    include_volatility: bool = True,
    include_moving_average: bool = True,
    include_liquidity: bool = True,
    include_regime: bool = True,
    include_regime_classification: bool = True,
) -> dict[str, object]:
    if now_utc is None:
        now_utc = datetime.now(UTC)
    cycle_wall_start = perf_counter()

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)
    dataset_registration_service = DatasetRegistrationService(session=session)

    resolved_universe_version_id = universe_version_id
    resolved_universe_source: str | None = None
    resolved_universe_member_count: int | None = None

    if symbols is None and universe_version_id is None:
        resolution_service = UniverseResolutionService(UniverseVersionRepository(session))
        active_version = resolution_service.resolve_active_or_none(now_utc)
        if active_version is not None:
            resolved_universe_version_id = active_version.universe_version_id
            resolved_universe_source = active_version.source
            universe_members = resolution_service.resolve_active_members(now_utc)
            resolved_universe_member_count = len(universe_members)
            symbols = universe_members if universe_members else None
    elif universe_version_id is not None:
        resolution_service = UniverseResolutionService(UniverseVersionRepository(session))
        active_version = resolution_service.resolve_active_or_none(now_utc)
        if active_version is not None and active_version.universe_version_id == universe_version_id:
            resolved_universe_source = active_version.source
            resolved_universe_member_count = resolution_service.resolve_member_count(now_utc)

    run_id = uuid.uuid4()
    component = "scheduler.run_feature_pipeline_cycle"
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
                job_name="feature_pipeline_cycle",
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
                    "price_basis": price_basis.value,
                    "dataset_version_id": dataset_version_id,
                    "symbols": symbols,
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

    base_metadata: dict[str, object] = {}
    manifest: RunManifest | None = None
    feature_results: list[dict[str, object]] = []

    record_cycle_started(
        logger=logger,
        metrics=FEATURE_PIPELINE_CYCLE_METRICS,
        component=component,
        run_id=str(run_id),
    )

    try:
        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.BACKTEST,
            created_at=now_utc,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id="feature_pipeline",
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal("10000.00"),
            interval=BarInterval.ONE_DAY,
            start_date=start_date or now_utc.date(),
            end_date=end_date or now_utc.date(),
            dataset_version=dataset_version_id or "latest_validated",
            universe_version=resolved_universe_version_id or "v1",
            universe_version_id=resolved_universe_version_id,
            universe_source=resolved_universe_source,
            universe_member_count=resolved_universe_member_count,
            git_commit="dev",
            python_version=platform.python_version(),
            notes="Feature engineering pipeline cycle",
            price_basis=price_basis,
            governance_state=GovernanceState.APPROVED_RESEARCH,
        )
        manifest_service.save(manifest)

        base_metadata = {
            "price_basis": price_basis.value,
            "dataset_version_id": dataset_version_id,
            "symbols": symbols,
            "universe_version_id": resolved_universe_version_id,
            "universe_member_count": resolved_universe_member_count,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "include_returns": include_returns,
            "include_volatility": include_volatility,
            "include_moving_average": include_moving_average,
            "include_liquidity": include_liquidity,
            "include_regime": include_regime,
            "include_regime_classification": include_regime_classification,
        }

        # Shared infrastructure
        parquet_bar_repository = ParquetBarRepository()
        feature_parquet_repository = ParquetFeatureRepository()
        feature_dataset_repository = FeatureDatasetVersionsRepository(session=session)

        resolver_service = FeatureDatasetResolverService(
            dataset_registration_service=dataset_registration_service,
            parquet_reader=parquet_bar_repository,
        )
        writer_service = FeatureDatasetWriterService(
            feature_dataset_repository=feature_dataset_repository,
            parquet_writer=feature_parquet_repository,
        )
        guard_service = FeaturePipelineGuardService(
            feature_dataset_repository=feature_dataset_repository,
        )
        validation_service = FeatureValidationService()

        _validate_feature_pipeline_lineage(
            dataset_version_id=dataset_version_id,
            price_basis=price_basis,
            dataset_registration_service=dataset_registration_service,
        )

        # Compute services
        returns_service = ReturnsFeatureService()
        volatility_service = VolatilityFeatureService()
        moving_average_service = MovingAverageFeatureService()
        liquidity_service = LiquidityFeatureService()
        regime_service = RegimeFeatureService()
        regime_classification_service = RegimeClassificationFeatureService()

        # Jobs
        returns_job = ReturnsFeatureJob(
            resolver_service=resolver_service,
            writer_service=writer_service,
            guard_service=guard_service,
            validation_service=validation_service,
            returns_service=returns_service,
        )
        volatility_job = VolatilityFeatureJob(
            resolver_service=resolver_service,
            writer_service=writer_service,
            guard_service=guard_service,
            validation_service=validation_service,
            returns_service=returns_service,
            volatility_service=volatility_service,
        )
        moving_average_job = MovingAverageFeatureJob(
            resolver_service=resolver_service,
            writer_service=writer_service,
            guard_service=guard_service,
            validation_service=validation_service,
            moving_average_service=moving_average_service,
        )
        liquidity_job = LiquidityFeatureJob(
            resolver_service=resolver_service,
            writer_service=writer_service,
            guard_service=guard_service,
            validation_service=validation_service,
            liquidity_service=liquidity_service,
        )
        regime_job = RegimeFeatureJob(
            resolver_service=resolver_service,
            writer_service=writer_service,
            guard_service=guard_service,
            validation_service=validation_service,
            regime_service=regime_service,
        )
        regime_classification_job = RegimeClassificationFeatureJob(
            resolver_service=resolver_service,
            writer_service=writer_service,
            guard_service=guard_service,
            validation_service=validation_service,
            regime_classification_service=regime_classification_service,
        )

        with start_span("feature_pipeline_cycle.run", timespan=SpanTimespan.CYCLE) as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.price_basis", price_basis.value)

            audit_logger.record_run_started(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            def record_feature_result(step: str, result: object) -> None:
                if not isinstance(result, FeatureDatasetVersion):
                    return

                feature_results.append(
                    {
                        "step": step,
                        "feature_dataset_version_id": result.dataset_version_id,
                        "feature_name": result.feature_name,
                        "source_dataset_version": result.source_dataset_version,
                        "validation_status": result.validation_status,
                        "storage_path": result.storage_path,
                        "computation_parameters": result.computation_parameters,
                    }
                )

            def run_step(step: str, fn: Callable[[], object]) -> object:
                record_step_started(
                    logger=logger,
                    metrics=FEATURE_PIPELINE_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                )

                step_start = perf_counter()
                try:
                    with start_span(
                        f"feature_pipeline_cycle.{step}",
                        timespan=SpanTimespan.STEP,
                    ) as step_span:
                        step_span.set_attribute("ratp.run_id", str(run_id))
                        step_span.set_attribute("ratp.step", step)
                        result = fn()

                    step_duration = perf_counter() - step_start
                    record_step_completed(
                        logger=logger,
                        metrics=FEATURE_PIPELINE_STEP_METRICS,
                        step=step,
                        component=component,
                        run_id=str(run_id),
                        duration_seconds=step_duration,
                    )
                    record_feature_result(step, result)
                    return result
                except Exception as exc:
                    step_duration = perf_counter() - step_start
                    record_step_failed(
                        logger=logger,
                        metrics=FEATURE_PIPELINE_STEP_METRICS,
                        step=step,
                        component=component,
                        run_id=str(run_id),
                        exc=exc,
                        duration_seconds=step_duration,
                    )
                    raise

            if include_returns:
                run_step(
                    "returns_feature",
                    lambda: returns_job.run(
                        price_basis=price_basis,
                        dataset_version_id=dataset_version_id,
                        symbols=symbols,
                        start_date=start_date,
                        end_date=end_date,
                    ),
                )

            if include_volatility:
                run_step(
                    "volatility_feature",
                    lambda: volatility_job.run(
                        price_basis=price_basis,
                        dataset_version_id=dataset_version_id,
                        symbols=symbols,
                        start_date=start_date,
                        end_date=end_date,
                        window=20,
                    ),
                )

            if include_moving_average:
                run_step(
                    "moving_average_feature_short",
                    lambda: moving_average_job.run(
                        price_basis=price_basis,
                        dataset_version_id=dataset_version_id,
                        symbols=symbols,
                        start_date=start_date,
                        end_date=end_date,
                        window=20,
                    ),
                )

                run_step(
                    "moving_average_feature_long",
                    lambda: moving_average_job.run(
                        price_basis=price_basis,
                        dataset_version_id=dataset_version_id,
                        symbols=symbols,
                        start_date=start_date,
                        end_date=end_date,
                        window=50,
                    ),
                )

            if include_liquidity:
                run_step(
                    "liquidity_feature",
                    lambda: liquidity_job.run(
                        price_basis=price_basis,
                        dataset_version_id=dataset_version_id,
                        symbols=symbols,
                        start_date=start_date,
                        end_date=end_date,
                        avg_volume_window=20,
                    ),
                )

            if include_regime:
                run_step(
                    "regime_feature",
                    lambda: regime_job.run(
                        price_basis=price_basis,
                        dataset_version_id=dataset_version_id,
                        symbols=symbols,
                        start_date=start_date,
                        end_date=end_date,
                        short_window=50,
                        long_window=200,
                    ),
                )

            if include_regime_classification:
                run_step(
                    "regime_classification_feature",
                    lambda: regime_classification_job.run(
                        price_basis=price_basis,
                        dataset_version_id=dataset_version_id,
                        symbols=symbols,
                        start_date=start_date,
                        end_date=end_date,
                    ),
                )

            output_summary: dict[str, object] = {
                "run_id": str(run_id),
                "runtime_job_run_id": job_run_id,
                "dataset_version_id": dataset_version_id,
                "last_successful_step": "feature_pipeline_completed",
                "feature_dataset_versions": feature_results,
                "include_returns": include_returns,
                "include_volatility": include_volatility,
                "include_moving_average": include_moving_average,
                "include_liquidity": include_liquidity,
                "include_regime": include_regime,
                "include_regime_classification": include_regime_classification,
            }

            manifest.status = "completed"
            _save_runtime_job_run(
                status="completed",
                completed_at=datetime.now(UTC),
                error_message=None,
                output_summary_json=output_summary,
            )

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
                metrics=FEATURE_PIPELINE_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )

            return output_summary

    except Exception as exc:
        total_duration = perf_counter() - cycle_wall_start

        _save_runtime_job_run(
            status="failed",
            completed_at=datetime.now(UTC),
            error_message=str(exc),
            output_summary_json={
                "dataset_version_id": dataset_version_id,
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
            metrics=FEATURE_PIPELINE_CYCLE_METRICS,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=total_duration,
        )
        raise
    finally:
        session.close()


if __name__ == "__main__":
    setup_telemetry("scheduler-feature-pipeline-cycle")
    run_feature_pipeline_cycle()
