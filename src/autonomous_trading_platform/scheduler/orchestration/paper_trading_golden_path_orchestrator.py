from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.runtime.interruptible_sleep import InterruptibleSleeper
from autonomous_trading_platform.runtime.services.pipeline_failure_notification_service import (
    PipelineFailureNotificationService,
)
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import (
    ChainContext,
    ChainResult,
    ChainStep,
    EodChainRunner,
)
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

_ET = ZoneInfo("America/New_York")

EOD_CHAIN_JOB_NAME = "paper_trading_eod_maintenance"


@dataclass(frozen=True)
class PaperTradingGoldenPathResult:
    correlation_id: str
    chain: ChainResult | None = None


class PaperTradingGoldenPathOrchestrator:
    """
    High-level orchestrator for the paper trading golden path.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.runner = RuntimeJobRunner(
            repository=RuntimeJobRunRepository(session),
            failure_notifier=PipelineFailureNotificationService(session),
        )

    def _get_active_daily_raw_bars_dataset(self, *, now_utc: datetime) -> DatasetVersions | None:
        trading_date = now_utc.date()
        row = (
            self.session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == "raw_bars")
            .filter(DatasetVersions.price_basis == PriceBasis.RAW.value)
            .filter(DatasetVersions.interval == BarInterval.FIVE_MIN.value)
            .filter(DatasetVersions.validation_status == "validated")
            .filter(DatasetVersions.date_coverage_start == trading_date)
            .filter(DatasetVersions.date_coverage_end == trading_date)
            .filter(
                DatasetVersions.metadata_json["dataset_lifecycle"].as_string()
                == "active_daily_incremental"
            )
            .order_by(DatasetVersions.created_at.desc())
            .first()
        )

        return row if isinstance(row, DatasetVersions) else None

    @staticmethod
    def _dataset_symbols(dataset_version: DatasetVersions) -> list[str]:
        source_manifest = dataset_version.source_manifest or {}
        symbols = source_manifest.get("symbols")
        if not isinstance(symbols, list) or not all(
            isinstance(symbol, str) and symbol for symbol in symbols
        ):
            raise RuntimeError(
                f"raw_bars dataset {dataset_version.dataset_version_id} does not declare symbols"
            )
        return sorted(symbols)

    def run_intraday_tick(
        self,
        *,
        now_utc: datetime,
    ) -> PaperTradingGoldenPathResult:
        correlation_id = str(uuid4())

        def _run_pipeline_chain() -> None:
            run_market_ingestion_cycle(now_utc=now_utc)

            latest_raw_bars = self._get_active_daily_raw_bars_dataset(now_utc=now_utc)

            if latest_raw_bars is None:
                raise RuntimeError("No raw_bars dataset version found after ingestion")

            run_feature_pipeline_cycle(
                now_utc=now_utc,
                price_basis=PriceBasis.RAW,
                dataset_version_id=latest_raw_bars.dataset_version_id,
                symbols=self._dataset_symbols(latest_raw_bars),
                start_date=latest_raw_bars.date_coverage_start,
                end_date=latest_raw_bars.date_coverage_end,
                include_returns=True,
                include_volatility=False,
                include_moving_average=False,
                include_liquidity=False,
                include_regime=False,
            )

            run_trading_cycle(now_utc=now_utc)

        self.runner.run(
            job_name="paper_trading_intraday_tick",
            trigger_type="scheduler",
            correlation_id=correlation_id,
            input_summary_json={
                "mode": "intraday_tick",
                "now_utc": now_utc.isoformat(),
                "steps": [
                    "market_ingestion_cycle",
                    "feature_pipeline_cycle",
                    "trading_cycle",
                ],
            },
            job=_run_pipeline_chain,
        )

        return PaperTradingGoldenPathResult(
            correlation_id=correlation_id,
        )

    def run(
        self,
        *,
        now_utc: datetime,
    ) -> PaperTradingGoldenPathResult:
        correlation_id = str(uuid4())

        def _run_pipeline_chain() -> None:
            run_market_ingestion_cycle(now_utc=now_utc)

            latest_raw_bars = self._get_active_daily_raw_bars_dataset(now_utc=now_utc)

            if latest_raw_bars is None:
                raise RuntimeError("No raw_bars dataset version found after ingestion")

            # Capture before any cycle closes the session (the row detaches).
            raw_bars_version_id = latest_raw_bars.dataset_version_id
            raw_bars_symbols = self._dataset_symbols(latest_raw_bars)

            run_feature_pipeline_cycle(
                now_utc=now_utc,
                price_basis=PriceBasis.RAW,
                dataset_version_id=raw_bars_version_id,
                symbols=raw_bars_symbols,
                start_date=latest_raw_bars.date_coverage_start,
                end_date=latest_raw_bars.date_coverage_end,
                include_returns=True,
                include_volatility=False,
                include_moving_average=False,
                include_liquidity=False,
                include_regime=False,
            )

            run_trading_cycle(now_utc=now_utc)

            run_corporate_action_ingestion_cycle(
                source_raw_bars_dataset_version_id=raw_bars_version_id,
                as_of=now_utc.date(),
                fetch_symbols=raw_bars_symbols,
            )

        self.runner.run(
            job_name="paper_trading_golden_path",
            trigger_type="scheduler",
            correlation_id=correlation_id,
            input_summary_json={
                "mode": "full_pipeline",
                "now_utc": now_utc.isoformat(),
                "steps": [
                    "market_ingestion_cycle",
                    "feature_pipeline_cycle",
                    "trading_cycle",
                    "corporate_action_ingestion_cycle",
                ],
            },
            job=_run_pipeline_chain,
        )

        return PaperTradingGoldenPathResult(
            correlation_id=correlation_id,
        )

    def run_eod_maintenance(
        self,
        *,
        now_utc: datetime,
        sleeper: InterruptibleSleeper | None = None,
    ) -> PaperTradingGoldenPathResult:
        """Run the end-of-day chain for the ET trading date of ``now_utc``.

        Each step is its own ``runtime_job_runs`` row under one parent per trading date;
        a chain stopped by a shutdown or crash resumes after its last completed step, and a
        chain that already completed or failed for the date is not re-run.
        """
        result = EodChainRunner(self.session, sleeper=sleeper).run(
            chain_name=EOD_CHAIN_JOB_NAME,
            trading_date=now_utc.astimezone(_ET).date(),
            now_utc=now_utc,
            steps=self.eod_chain_steps(),
        )
        return PaperTradingGoldenPathResult(
            correlation_id=result.correlation_id or str(uuid4()),
            chain=result,
        )

    def eod_chain_steps(self) -> list[ChainStep]:
        return [
            ChainStep(
                "resolve_raw_bars_dataset",
                self._step_resolve_raw_bars_dataset,
                blocking=True,
                max_attempts=1,
            ),
            ChainStep("corporate_actions", self._step_corporate_actions, blocking=True),
            ChainStep("features", self._step_features, blocking=True),
        ]

    # -- end-of-day steps -------------------------------------------------------------

    def _step_resolve_raw_bars_dataset(self, ctx: ChainContext) -> dict[str, Any]:
        latest_raw_bars = self._get_active_daily_raw_bars_dataset(now_utc=ctx.now_utc)
        if latest_raw_bars is None:
            raise RuntimeError("No active daily raw_bars dataset found for EOD maintenance")

        # Capture all values before any cycle closes the session
        ctx.values.update(
            dataset_version_id=latest_raw_bars.dataset_version_id,
            symbols=self._dataset_symbols(latest_raw_bars),
            date_coverage_start=latest_raw_bars.date_coverage_start,
            date_coverage_end=latest_raw_bars.date_coverage_end,
            interval=latest_raw_bars.interval,
            symbol_coverage=latest_raw_bars.symbol_coverage,
        )
        return {
            "dataset_version_id": latest_raw_bars.dataset_version_id,
            "symbol_count": len(ctx.values["symbols"]),
        }

    def _step_corporate_actions(self, ctx: ChainContext) -> dict[str, Any]:
        run_corporate_action_ingestion_cycle(
            source_raw_bars_dataset_version_id=ctx.values["dataset_version_id"],
            as_of=ctx.now_utc.date(),
            fetch_symbols=ctx.values["symbols"],
        )
        return {"dataset_version_id": ctx.values["dataset_version_id"]}

    def _step_features(self, ctx: ChainContext) -> dict[str, Any]:
        v = ctx.values
        # Features run on the day's raw version; the feature pipeline split-adjusts
        # history on read from the stored corporate actions (plan 5d, D5). The
        # materialised adjusted-bars dataset is retired.
        try:
            run_feature_pipeline_cycle(
                now_utc=ctx.now_utc,
                price_basis=PriceBasis.RAW,
                dataset_version_id=v["dataset_version_id"],
                symbols=v["symbols"],
                start_date=v["date_coverage_start"],
                end_date=v["date_coverage_end"],
                include_returns=True,
                include_volatility=False,
                include_moving_average=False,
                include_liquidity=False,
                include_regime=False,
            )
        except ValueError as exc:
            # The cycle records a day with no bars as a skipped run itself; this
            # guard only remains for an older cycle that still raises.
            if not str(exc).startswith("No bar data found for dataset_version_id="):
                raise
        features_version_id = generate_dataset_version("features")
        self.session.add(
            DatasetVersions(
                dataset_version_id=features_version_id,
                dataset_name="features",
                created_at=ctx.now_utc,
                source="feature_pipeline",
                price_basis=PriceBasis.RAW,
                interval=v["interval"],
                schema_version="1.0.0",
                symbol_coverage=v["symbol_coverage"],
                date_coverage_start=v["date_coverage_start"],
                date_coverage_end=v["date_coverage_end"],
                validation_status="validated",
                checksum=None,
                source_dataset_version=v["dataset_version_id"],
                source_manifest={
                    "source_raw_bars_version": v["dataset_version_id"],
                    "pipeline": "feature_pipeline",
                    "split_adjusted_on_read": True,
                },
                metadata_json={
                    "price_basis": PriceBasis.RAW.value,
                    "source_raw_bars_version": v["dataset_version_id"],
                    "stage": "eod",
                },
            )
        )
        self.session.flush()
        ctx.values["features_dataset_version_id"] = features_version_id
        return {"features_dataset_version_id": features_version_id}
