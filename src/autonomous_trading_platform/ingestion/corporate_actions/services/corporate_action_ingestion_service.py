from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.ingestion.corporate_actions.clients import (
    alpaca_corporate_action_client as client,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_adjustment_service import (
    CorporateActionAdjustmentService,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_normalization_service import (
    CorporateActionNormalizationService,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_validation_service import (
    CorporateActionValidationService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    record_operation_completed,
    record_operation_failed,
    record_operation_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    actions_per_symbol,
    adjustment_failures,
    adjustments_applied,
    affected_bars_per_action,
    corporate_action_adjustment_duration_seconds,
    corporate_action_ingestion_batch_size,
    corporate_action_normalization_failures,
    corporate_action_processing_duration_seconds,
    corporate_action_records_processed,
    corporate_action_request_latency_seconds,
    corporate_action_validation_failures,
    corporate_actions_ingested,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.storage.parquet.parquet_bar_repository import (
    ParquetBarRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)


@dataclass
class CorporateActionProcessingResult:
    created_actions: list[CorporateAction]
    adjusted_bars: list[MarketBar]


class CorporateActionIngestionService:
    def __init__(
        self,
        *,
        session: Session,
        run_id: str,
        audit_logger: Any,
        cycle_timestamp: datetime,
        bar_repository: ParquetBarRepository,
        raw_bars_dataset_version_id: str,
    ) -> None:
        self.session = session
        self.normalization_service = CorporateActionNormalizationService()
        self.validation_service = CorporateActionValidationService()
        self.adjustment_service = CorporateActionAdjustmentService()
        self.run_id = run_id
        self.cycle_timestamp = cycle_timestamp
        self.audit_logger = audit_logger
        self.bar_repository = bar_repository
        self.raw_bars_dataset_version_id = raw_bars_dataset_version_id

    def ingest_corporate_actions(self) -> CorporateActionProcessingResult:
        component = "ingestion.corporate_action_ingestion_service"

        record_operation_started(
            logger=logger,
            event="corporate_action_ingestion_service_started",
            run_id=self.run_id,
            component=component,
            cycle_timestamp=self.cycle_timestamp.isoformat(),
        )

        request_start = perf_counter()
        try:
            with start_span(
                "corporate_action_ingestion_service.fetch_corporate_actions",
                timespan=SpanTimespan.REQUEST,
            ) as request_span:
                request_span.set_attribute("ratp.run_id", self.run_id)
                request_span.set_attribute("ratp.component", component)
                request_span.set_attribute("ratp.cycle_timestamp", self.cycle_timestamp.isoformat())

                payload: dict = client.fetch_corporate_actions()

            request_duration = perf_counter() - request_start
            corporate_action_request_latency_seconds.record(
                request_duration,
                {
                    "component": component,
                    "status": "completed",
                },
            )
        except Exception:
            request_duration = perf_counter() - request_start
            corporate_action_request_latency_seconds.record(
                request_duration,
                {
                    "component": component,
                    "status": "failed",
                },
            )
            raise
        actions_block = payload.get("corporate_actions", {})
        raw_actions = actions_block.get("cash_dividends", []) + actions_block.get(
            "reverse_splits", []
        )

        corporate_action_ingestion_batch_size.record(
            len(raw_actions),
            {
                "component": component,
            },
        )

        record_operation_completed(
            logger=logger,
            event="corporate_action_ingestion_service_fetch_completed",
            run_id=self.run_id,
            component=component,
            raw_action_count=len(raw_actions),
            request_duration=request_duration,
        )

        with start_span(
            "corporate_action_ingestion_service.ingest", timespan=SpanTimespan.STEP
        ) as service_span:
            service_span.set_attribute("ratp.run_id", self.run_id)
            service_span.set_attribute("ratp.component", component)
            service_span.set_attribute("ratp.raw_action_count", len(raw_actions))

            created_actions: list[CorporateAction] = []
            all_adjusted_bars: list[MarketBar] = []

            with SorUnitOfWork(self.session) as uow:
                for raw_action in raw_actions:
                    symbol = raw_action.get("symbol", "UNKNOWN")
                    processing_start = perf_counter()

                    corporate_action_records_processed.add(
                        1,
                        {
                            "component": component,
                            "symbol": symbol,
                        },
                    )

                    with start_span(
                        "corporate_action_ingestion_service.normalize_action",
                        timespan=SpanTimespan.STEP,
                    ) as normalization_span:
                        normalization_span.set_attribute("ratp.run_id", self.run_id)
                        normalization_span.set_attribute(
                            "ratp.symbol", raw_action.get("symbol", "UNKNOWN")
                        )
                        try:
                            action = self.normalization_service.parse_alpaca_corporate_action(
                                raw_action
                            )
                            normalization_span.set_attribute("ratp.normalization.failed", False)
                            normalization_span.set_attribute(
                                "ratp.action_type", action.action_type.value
                            )
                            normalization_span.set_attribute(
                                "ratp.effective_date",
                                action.effective_date.isoformat(),
                            )
                        except ValueError:
                            normalization_span.set_attribute("ratp.normalization.failed", True)
                            corporate_action_normalization_failures.add(
                                1,
                                {
                                    "component": component,
                                    "symbol": symbol,
                                },
                            )
                            self.audit_logger.record_corporate_action_parse_failed(
                                run_id=self.run_id,
                                symbol=symbol,
                                cycle_timestamp=self.cycle_timestamp,
                            )
                            continue

                    actions_per_symbol.record(
                        1,
                        {
                            "component": component,
                            "symbol": action.symbol,
                        },
                    )

                    with start_span(
                        "corporate_action_ingestion_service.validate_action",
                        timespan=SpanTimespan.STEP,
                    ) as validation_span:
                        validation_span.set_attribute("ratp.run_id", self.run_id)
                        validation_span.set_attribute("ratp.symbol", action.symbol)
                        validation_result = self.validation_service.validate(action)
                        if not validation_result.ok:
                            corporate_action_validation_failures.add(
                                1,
                                {
                                    "component": component,
                                    "symbol": action.symbol,
                                },
                            )
                            self.audit_logger.record_corporate_action_validation_failed(
                                run_id=self.run_id,
                                symbol=action.symbol,
                                cycle_timestamp=self.cycle_timestamp,
                            )
                            continue

                    with start_span(
                        "corporate_action_ingestion_service.persist_action",
                        timespan=SpanTimespan.STEP,
                    ) as persistence_span:
                        persistence_span.set_attribute("ratp.run_id", self.run_id)
                        persistence_span.set_attribute("ratp.symbol", action.symbol)
                        result = uow.corporate_actions.upsert(action)

                    if not result.created:
                        continue

                    created_actions.append(action)

                    corporate_actions_ingested.add(
                        1,
                        {
                            "component": component,
                            "symbol": action.symbol,
                            "action_type": action.action_type.value,
                        },
                    )

                    if not self.adjustment_service.supports_adjustment(action):
                        continue

                    adjustment_start = perf_counter()
                    try:
                        raw_bar_contracts = self.bar_repository.get_raw_bars_before_date(
                            symbol=action.symbol,
                            dataset_version=self.raw_bars_dataset_version_id,
                            effective_date=action.effective_date,
                        )

                        adjusted_bars = self.adjustment_service.apply_action_to_bars(
                            action,
                            raw_bar_contracts,
                        )
                        all_adjusted_bars.extend(adjusted_bars)

                        affected_bars_per_action.record(
                            len(adjusted_bars),
                            {
                                "component": component,
                                "symbol": action.symbol,
                                "action_type": action.action_type.value,
                            },
                        )

                        adjustments_applied.add(
                            1,
                            {
                                "component": component,
                                "symbol": action.symbol,
                                "action_type": action.action_type.value,
                            },
                        )

                        corporate_action_adjustment_duration_seconds.record(
                            perf_counter() - adjustment_start,
                            {
                                "component": component,
                                "symbol": action.symbol,
                                "status": "completed",
                            },
                        )

                        self.audit_logger.record_corporate_action_adjustment_applied(
                            run_id=self.run_id,
                            symbol=action.symbol,
                            cycle_timestamp=self.cycle_timestamp,
                        )
                    except Exception as exc:
                        adjustment_failures.add(
                            1,
                            {
                                "component": component,
                                "symbol": action.symbol,
                            },
                        )
                        corporate_action_adjustment_duration_seconds.record(
                            perf_counter() - adjustment_start,
                            {
                                "component": component,
                                "symbol": action.symbol,
                                "status": "failed",
                            },
                        )

                        record_operation_failed(
                            logger=logger,
                            event="corporate_action_adjustment_failed",
                            run_id=self.run_id,
                            component=component,
                            exc=exc,
                            action_symbol=action.symbol,
                        )
                        raise
                    finally:
                        corporate_action_processing_duration_seconds.record(
                            perf_counter() - processing_start,
                            {
                                "component": component,
                                "symbol": action.symbol,
                            },
                        )

        record_operation_completed(
            logger=logger,
            event="corporate_action_ingestion_service_completed",
            run_id=self.run_id,
            component=component,
        )
        return CorporateActionProcessingResult(
            created_actions=created_actions,
            adjusted_bars=all_adjusted_bars,
        )
