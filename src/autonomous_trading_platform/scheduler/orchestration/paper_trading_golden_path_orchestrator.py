from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.dataset_publish_service import (
    DatasetPublishService,
)
from autonomous_trading_platform.application.services.platform_replay.governance_hooks import (
    run_governance_at_timestamp,
)
from autonomous_trading_platform.application.services.platform_replay.operations_hooks import (
    snapshot_operations_at_timestamp,
)
from autonomous_trading_platform.application.services.platform_replay.risk_hooks import (
    run_risk_at_timestamp,
)
from autonomous_trading_platform.application.services.platform_replay.universe_hooks import (
    run_universe_at_timestamp,
)
from autonomous_trading_platform.application.services.portfolio_review_service import (
    PortfolioReviewService,
    review_mode,
)
from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
    PortfolioScorecardService,
)
from autonomous_trading_platform.application.services.rolling_raw_bars_service import (
    RollingRawBarsService,
)
from autonomous_trading_platform.application.services.worker_result_apply_service import (
    WorkerResultApplyService,
)
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.governance.portfolio_review import PortfolioReviewMode
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.services.external_broker_reconciliation_service import (
    BrokerClient,
    ExternalBrokerReconciliationService,
)
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    StrategySleeveLedgerService,
    fetch_account_positions,
    plain_quantity,
)
from autonomous_trading_platform.runtime.clock import MarketCalendar, RealMarketCalendar
from autonomous_trading_platform.runtime.interruptible_sleep import InterruptibleSleeper
from autonomous_trading_platform.runtime.services.pipeline_failure_notification_service import (
    PipelineFailureNotificationService,
)
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle import (
    run_allocation_rebalance_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_correlation_monitoring_cycle import (
    run_correlation_monitoring_cycle,
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
from autonomous_trading_platform.scheduler.services.market_calendar_cross_check import (
    run_calendar_cross_check,
)
from autonomous_trading_platform.storage.parquet.object_store import ObjectStore, S3ObjectStore
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.reconciliation_snapshot_repository import (
    ReconciliationSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

_ET = ZoneInfo("America/New_York")

EOD_CHAIN_JOB_NAME = "paper_trading_eod_maintenance"
# Job name for one step run on demand. Deliberately not the chain's name: the chain's
# resume logic reads rows by that name and must never count a manual run.
EOD_STEP_JOB_NAME = "paper_trading_eod_step"

_RESOLVE_STEP = "resolve_raw_bars_dataset"
_WEEKLY_REVIEW_STEP = "weekly_portfolio_review"
# Steps that change what trades. Off their scheduled day they are never run for real.
_SCHEDULE_BOUND_STEPS = frozenset({"monthly_universe_rotation"})
# Where to rehearse a step that run-eod-step cannot dry-run.
_REHEARSE_WITH = {
    "final_ingestion": "atp ingestion run-bars --dry-run",
    "corporate_actions": "atp ingestion run-corporate-actions --dry-run",
    "monthly_universe_rotation": "atp universe monthly-rotation --dry-run",
}


@dataclass(frozen=True)
class PaperTradingGoldenPathResult:
    correlation_id: str
    chain: ChainResult | None = None


class EodStepRefused(ValueError):
    """A single-step run was not allowed as asked. Nothing was run."""


def _rehearsal_hint(step_name: str) -> str:
    command = _REHEARSE_WITH.get(step_name)
    return f"; rehearse it with `{command}`" if command else ""


@dataclass(frozen=True)
class EodStepResult:
    step: str
    trading_date: date
    now_utc: datetime
    dry_run: bool
    # Whether the scheduled chain runs this step on trading_date (its calendar gate).
    scheduled_for_date: bool
    status: str
    job_run_id: str
    output: dict[str, Any] | None = None
    error: str | None = None


class PaperTradingGoldenPathOrchestrator:
    """
    High-level orchestrator for the paper trading golden path.
    """

    def __init__(
        self,
        session: Session,
        *,
        broker_client_factory: Callable[[], BrokerClient] | None = None,
        calendar: MarketCalendar | None = None,
        object_store_factory: Callable[[Settings], ObjectStore] | None = None,
    ) -> None:
        self.session = session
        self._calendar = calendar or RealMarketCalendar()
        self._object_store_factory = object_store_factory or (
            lambda settings: S3ObjectStore(
                settings.dataset_s3_bucket or "", region=settings.dataset_s3_region
            )
        )
        self._broker_client_factory = broker_client_factory or (
            lambda: AlpacaBrokerClient(Settings())
        )
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
        """The end-of-day chain, in the backtester's hook order (plan 1.3 §2).

        Blocking steps produce the data the rest of the day needs; the governance block
        is independent so one failing service leaves the others running, exactly as the
        replay hooks swallow per-service errors into warnings.
        """
        # rollback_safe marks the steps whose every effect goes through self.session
        # (broker and calendar calls in them are reads). The others fetch and store
        # through a session of their own, write Parquet, or upload; the rotation's
        # candidate scoring commits on its own session (run_candidate_generation).
        return [
            ChainStep(
                "calendar_cross_check",
                self._step_calendar_cross_check,
                max_attempts=2,
                rollback_safe=True,
            ),
            ChainStep("final_ingestion", self._step_final_ingestion),
            ChainStep(
                "resolve_raw_bars_dataset",
                self._step_resolve_raw_bars_dataset,
                blocking=True,
                max_attempts=1,
                rollback_safe=True,
            ),
            ChainStep(
                "broker_reconciliation", self._step_broker_reconciliation, rollback_safe=True
            ),
            ChainStep(
                "sleeve_resync",
                self._step_sleeve_resync,
                applies=lambda ctx: self._portfolio_mode_enabled(),
                rollback_safe=True,
            ),
            ChainStep("corporate_actions", self._step_corporate_actions, blocking=True),
            ChainStep("features", self._step_features, blocking=True),
            ChainStep("risk", self._step_risk, rollback_safe=True),
            ChainStep("governance", self._step_governance, rollback_safe=True),
            ChainStep(
                "correlation_monitoring", self._step_correlation_monitoring, rollback_safe=True
            ),
            ChainStep("allocation_rebalance", self._step_allocation_rebalance, rollback_safe=True),
            ChainStep(
                "weekly_portfolio_review",
                self._step_weekly_portfolio_review,
                applies=lambda ctx: self._calendar.is_first_trading_day_of_week(ctx.trading_date),
                rollback_safe=True,
            ),
            ChainStep(
                "monthly_universe_rotation",
                self._step_monthly_universe_rotation,
                applies=lambda ctx: self._calendar.is_last_trading_day_of_month(ctx.trading_date),
            ),
            ChainStep("rolling_raw_bars", self._step_rolling_raw_bars),
            ChainStep("operations_health", self._step_operations_health, rollback_safe=True),
            ChainStep(
                "publish_datasets",
                self._step_publish_datasets,
                applies=lambda ctx: Settings().dataset_s3_bucket is not None,
            ),
        ]

    def run_eod_step(
        self,
        step_name: str,
        *,
        trading_date: date,
        dry_run: bool,
        force: bool = False,
        actor: str | None = None,
    ) -> EodStepResult:
        """Run one step of the end-of-day chain for ``trading_date``, outside the chain.

        It is the chain's own step function with the clock set to when the chain runs
        for that date. The step's calendar gate is reported, not enforced: rehearsing a
        weekly or monthly step off its day is the point. A step that comes after
        ``resolve_raw_bars_dataset`` gets that step's values first, as it does in the
        chain. One attempt, no retries.

        With ``dry_run`` the caller must pass a session whose work is rolled back
        (``db.rollback_only_session``); only steps marked ``rollback_safe`` are accepted.
        A real run is recorded as an ``EOD_STEP_JOB_NAME`` job run.
        """
        steps = self.eod_chain_steps()
        names = [step.name for step in steps]
        if step_name not in names:
            raise EodStepRefused(f"unknown step {step_name!r}; the steps are: {', '.join(names)}")
        step = steps[names.index(step_name)]
        if dry_run and not step.rollback_safe:
            raise EodStepRefused(
                f"{step_name} has effects a rollback cannot undo (its own database "
                "session, files or uploads), so it has no dry run here" + _rehearsal_hint(step_name)
            )

        now_utc = self._calendar.eod_window_open(trading_date)
        job_run_id = str(uuid4())
        ctx = ChainContext(
            chain_name=EOD_STEP_JOB_NAME,
            trading_date=trading_date,
            now_utc=now_utc,
            session=self.session,
            parent_job_run_id=job_run_id,
        )
        scheduled = self._calendar.is_trading_day(trading_date) and (
            step.applies is None or step.applies(ctx)
        )
        self._check_eod_step_allowed(
            step_name, trading_date=trading_date, scheduled=scheduled, dry_run=dry_run, force=force
        )

        runs = RuntimeJobRunRepository(self.session)
        started_at = datetime.now(UTC)

        def job_row(
            status: str, *, output: dict[str, Any] | None = None, error: str | None = None
        ) -> RuntimeJobRun:
            completed_at = None if status == "running" else datetime.now(UTC)
            return RuntimeJobRun(
                job_run_id=job_run_id,
                job_name=EOD_STEP_JOB_NAME,
                parent_job_run_id=None,
                status=status,
                trigger_type="manual_cli",
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=(
                    int((completed_at - started_at).total_seconds() * 1000)
                    if completed_at is not None
                    else None
                ),
                error_message=error[:2000] if error else None,
                correlation_id=job_run_id,
                input_summary_json={
                    "step": step_name,
                    "trading_date": trading_date.isoformat(),
                    "now_utc": now_utc.isoformat(),
                    "dry_run": dry_run,
                    "scheduled_for_date": scheduled,
                    "forced": force,
                    "actor": actor,
                },
                output_summary_json=output,
            )

        def result(
            status: str, *, output: dict[str, Any] | None = None, error: str | None = None
        ) -> EodStepResult:
            return EodStepResult(
                step=step_name,
                trading_date=trading_date,
                now_utc=now_utc,
                dry_run=dry_run,
                scheduled_for_date=scheduled,
                status=status,
                job_run_id=job_run_id,
                output=output,
                error=error,
            )

        runs.save(job_row("running"))
        try:
            if names.index(step_name) > names.index(_RESOLVE_STEP):
                self._step_resolve_raw_bars_dataset(ctx)
            raw_output = step.run(ctx)
            output = dict(raw_output) if raw_output is not None else None
        except Exception as exc:
            self.session.rollback()
            runs.save(job_row("failed", error=str(exc)))
            return result("failed", error=str(exc))
        runs.save(job_row("completed", output=output))
        return result("completed", output=output)

    def _check_eod_step_allowed(
        self,
        step_name: str,
        *,
        trading_date: date,
        scheduled: bool,
        dry_run: bool,
        force: bool,
    ) -> None:
        if not dry_run and not scheduled:
            if step_name in _SCHEDULE_BOUND_STEPS:
                raise EodStepRefused(
                    f"{step_name} is not scheduled for {trading_date}; off its scheduled "
                    "day it can only be rehearsed" + _rehearsal_hint(step_name)
                )
            if (
                step_name == _WEEKLY_REVIEW_STEP
                and review_mode(self.session) is PortfolioReviewMode.AUTO
            ):
                raise EodStepRefused(
                    f"{step_name} is not scheduled for {trading_date} and "
                    "portfolio_review_mode is auto, so it would apply decisions; "
                    "run it with --dry-run"
                )
        if force:
            return
        chain_running = self.session.scalars(
            select(RuntimeJobRuns.job_run_id)
            .where(RuntimeJobRuns.job_name == EOD_CHAIN_JOB_NAME)
            .where(RuntimeJobRuns.status == "running")
        ).first()
        if chain_running is not None:
            raise EodStepRefused(
                f"the scheduled chain is running (job run {chain_running}); wait for it, "
                "or pass --force if that row is a leftover from a crash"
            )
        if not dry_run and step_name in EodChainRunner(self.session).completed_steps_for(
            chain_name=EOD_CHAIN_JOB_NAME, trading_date=trading_date
        ):
            raise EodStepRefused(
                f"the scheduled chain already completed {step_name} for {trading_date}; "
                "pass --force to run it a second time, or --dry-run to rehearse it"
            )

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

    def _step_final_ingestion(self, ctx: ChainContext) -> dict[str, Any]:
        """Store the session's last five-minute bar.

        No intraday tick can: a tick fetches the window that ended before it started,
        and the last tick starts before the close. The window comes from the calendar,
        so an early close gets its own last bar (12:55-13:00 ET).
        """
        close_utc = self._calendar.market_close(ctx.trading_date)
        window_start = close_utc - timedelta(minutes=5)
        run_market_ingestion_cycle(
            now_utc=ctx.now_utc,
            cycle_start_override=window_start,
            cycle_end_override=close_utc,
            # Fetched on purpose after the close: not a slow tick, so no LATE flag, and
            # thin names missing this one bar must not fail the step.
            enforce_lateness=False,
        )
        return {
            "now_utc": ctx.now_utc.isoformat(),
            "cycle_start": window_start.isoformat(),
            "cycle_end": close_utc.isoformat(),
        }

    def _step_broker_reconciliation(self, ctx: ChainContext) -> dict[str, Any]:
        """Read-only comparison of platform state with the broker; the report is persisted."""
        settings = Settings()
        report = ExternalBrokerReconciliationService(
            broker_client=self._broker_client_factory(),
            session=self.session,
            environment=settings.trading_environment.value,
        ).reconcile(ctx.parent_job_run_id, now=ctx.now_utc)
        ReconciliationSnapshotRepository(self.session).append_report(report)
        self.session.flush()
        failed_checks = [
            f"{check.check_type.value}:{check.symbol}" if check.symbol else check.check_type.value
            for check in report.checks
            if check.status.value != "passed"
        ]
        return {
            "report_id": str(report.report_id),
            "overall_status": report.overall_status.value,
            "check_count": len(report.checks),
            "failed_checks": failed_checks,
        }

    def _portfolio_mode_enabled(self) -> bool:
        return bool(
            OperatorSettingsRepository(self.session).get_or_create_default().portfolio_mode_enabled
        )

    def _step_sleeve_resync(self, ctx: ChainContext) -> dict[str, Any]:
        """Make the sleeve books sum to the broker account (Finding 24): sleeves that
        claim shares the account does not hold are written down, unowned shares are
        adopted. A symbol with an order still open is left for the next session's
        first tick, which applies the fill before evaluating."""
        account_positions = fetch_account_positions(self._broker_client_factory())
        with SorUnitOfWork(self.session) as uow:
            open_symbols = {
                str(row.symbol).upper() for row in uow.tracked_orders.list_reconcilable_orders()
            }
            report = StrategySleeveLedgerService().resync(
                uow,
                account_positions=account_positions,
                timestamp=ctx.now_utc,
                skip_symbols=open_symbols,
            )
        self.session.flush()
        return {
            "adopted_symbols": list(report.adopted_symbols),
            "written_down": [
                {
                    "strategy_id": w.strategy_id,
                    "symbol": w.symbol,
                    "quantity": plain_quantity(w.quantity),
                }
                for w in report.written_down
            ],
            "skipped_symbols": [m.symbol for m in report.skipped],
            "unresolved": {m.symbol: plain_quantity(m.difference) for m in report.unresolved},
        }

    def _live_context(self, ctx: ChainContext) -> PlatformReplayContext:
        """The context the replay hooks take; here it carries the live chain's identity."""
        context = PlatformReplayContext.create(
            symbols=list(ctx.values.get("symbols", [])),
            timestamp=ctx.now_utc,
            actor="scheduler",
        )
        context.dataset_version_id = ctx.values.get("dataset_version_id")
        return context

    def _step_risk(self, ctx: ChainContext) -> dict[str, Any]:
        """Risk snapshot → drawdown ladder → advisory risk budget (same code as the replay)."""
        result = run_risk_at_timestamp(
            session=self.session, timestamp=ctx.now_utc, replay_context=self._live_context(ctx)
        )
        self.session.flush()
        return {**result.summary, "warnings": list(result.warnings)}

    def _step_governance(self, ctx: ChainContext) -> dict[str, Any]:
        """Live + shadow metrics → promotion → demotion → health lifecycle (same code as the replay)."""
        result = run_governance_at_timestamp(
            session=self.session, timestamp=ctx.now_utc, replay_context=self._live_context(ctx)
        )
        self.session.flush()
        return {**result.summary, "warnings": list(result.warnings)}

    def _step_correlation_monitoring(self, ctx: ChainContext) -> dict[str, Any]:
        result = run_correlation_monitoring_cycle(trigger_source="scheduler", session=self.session)
        return dict(result) if isinstance(result, dict) else {"status": str(result)}

    def _step_allocation_rebalance(self, ctx: ChainContext) -> dict[str, Any]:
        """Interim weekly re-weight trigger until the portfolio review is on (plan 1.3 §4.1);
        the engine's own interval guard and auto_rebalance_enabled switch gate it."""
        result = run_allocation_rebalance_cycle(
            now_utc=ctx.now_utc,
            trigger_source="scheduler",
            session=self.session,
            broker_client=self._broker_client_factory(),
        )
        return dict(result) if isinstance(result, dict) else {"status": str(result)}

    def _step_rolling_raw_bars(self, ctx: ChainContext) -> dict[str, Any]:
        """One raw-bars version spanning the bench window (Finding 14), rebuilt from the
        per-session versions whenever a new session has landed; published with the rest."""
        from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
            OperatorSettingsRepository,
        )

        settings = OperatorSettingsRepository(self.session).get_or_create_default()
        sessions = int(getattr(settings, "bench_resim_window_days", 63) or 63)
        result = RollingRawBarsService(self.session).build(
            as_of=ctx.trading_date, sessions=sessions, now_utc=ctx.now_utc
        )
        self.session.flush()
        return result.summary()

    def _step_operations_health(self, ctx: ChainContext) -> dict[str, Any]:
        result = snapshot_operations_at_timestamp(
            session=self.session, timestamp=ctx.now_utc, replay_context=self._live_context(ctx)
        )
        return {**result.summary, "warnings": list(result.warnings)}

    def _step_weekly_portfolio_review(self, ctx: ChainContext) -> dict[str, Any]:
        """Weekly re-weight / monthly swap review. Does nothing while
        ``portfolio_review_mode`` is off. Uses the worker's latest accepted, still-fresh
        bench re-sims when there are any (plan 3.2) and marks that result applied;
        without one the review runs on forward and backtest evidence alone."""
        mode = review_mode(self.session)
        if mode.value == "off":
            return {"mode": mode.value, "review_id": None}
        apply = WorkerResultApplyService(self.session)
        resims = apply.latest_bench_resims(now=ctx.now_utc)
        result = PortfolioReviewService(
            self.session, scorecards=PortfolioScorecardService(self.session)
        ).run(
            now=ctx.now_utc,
            resim_outcomes=resims.outcomes if resims else None,
            window_start=resims.window_start if resims else None,
            window_end=resims.window_end if resims else None,
            market_returns=resims.market_returns if resims else None,
            worker_run_id=resims.run_id if resims else None,
        )
        self.session.flush()
        if result is None:
            return {"mode": mode.value, "review_id": None}
        if resims is not None:
            apply.mark_applied(resims.run_id, review_id=result.review_id, now=ctx.now_utc)
            self.session.flush()
        return {
            "mode": result.mode.value,
            "review_id": result.review_id,
            "worker_run_id": resims.run_id if resims else None,
            "kill_switch_blocked": result.kill_switch_blocked,
            "swap_eligible": result.swap_eligible,
            "decisions": [
                {
                    "type": d.decision_type.value,
                    "strategy_id": d.strategy_id,
                    "applied": d.applied,
                    "reason": d.reason,
                }
                for d in result.decisions
            ],
        }

    def _step_monthly_universe_rotation(self, ctx: ChainContext) -> dict[str, Any]:
        """Raw pool refresh → candidates → rotation, at the close of the month's last
        session so the new universe is in force for the first. Unlike replays the churn
        guard stays on."""
        result = run_universe_at_timestamp(
            session=self.session,
            timestamp=ctx.now_utc,
            replay_context=self._live_context(ctx),
            force_rotation=False,
            rotation_reason="scheduler_monthly",
            # Finding 16: the pool is ~500 names but this box only ingests bars for the
            # ones it trades, so the scorer fetches Alpaca daily bars for the pool.
            candidate_market_data="alpaca_daily",
        )
        self.session.flush()
        if result.status == "failed":
            raise RuntimeError("; ".join(result.errors) or "universe rotation failed")
        return {**result.summary, "status": result.status, "warnings": list(result.warnings)}

    def _step_calendar_cross_check(self, ctx: ChainContext) -> dict[str, Any]:
        """Compare the next two weeks of the local calendar with the broker's; report only."""
        return run_calendar_cross_check(
            self._calendar, now_utc=ctx.now_utc, broker_sessions=self._broker_sessions()
        )

    def _broker_sessions(self) -> Any:
        """Hook for tests; None makes the step fetch Alpaca's calendar."""
        return None

    def _step_publish_datasets(self, ctx: ChainContext) -> dict[str, Any]:
        """Upload every Parquet dataset version not yet in the object store, then the day's
        index. Idempotent; nothing is deleted from the store."""
        settings = Settings()
        result = DatasetPublishService(
            self._object_store_factory(settings),
            session=self.session,
            prefix=settings.dataset_s3_prefix,
            git_sha=settings.git_sha,
        ).publish_new_versions(trading_date=ctx.trading_date, now_utc=ctx.now_utc)
        return result.summary()
