from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.ingestion.corporate_actions.clients import (
    alpaca_corporate_action_client as client,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_normalization_service import (
    ALPACA_LIST_KEY_TO_PROVIDER_TYPE,
    CorporateActionNormalizationService,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_validation_service import (
    CorporateActionValidationService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    record_operation_completed,
    record_operation_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    actions_per_symbol,
    corporate_action_ingestion_batch_size,
    corporate_action_normalization_failures,
    corporate_action_processing_duration_seconds,
    corporate_action_records_processed,
    corporate_action_request_latency_seconds,
    corporate_action_validation_failures,
    corporate_actions_ingested,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

# Action types the platform applies to its books (splits and cash dividends). Every
# other stored type is surfaced for manual review: the books are not changed for it.
APPLIED_ACTION_TYPES: frozenset[CorporateActionType] = frozenset(
    {
        CorporateActionType.SPLIT_FORWARD,
        CorporateActionType.SPLIT_REVERSE,
        CorporateActionType.CASH_DIVIDEND,
    }
)


@dataclass
class CorporateActionIngestionCounts:
    fetched: int = 0
    unsupported: int = 0
    parse_failed: int = 0
    validation_failed: int = 0
    created: int = 0
    updated: int = 0
    manual_review: int = 0
    unsupported_list_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CorporateActionProcessingResult:
    created_actions: list[CorporateAction]
    updated_actions: list[CorporateAction] = field(default_factory=list)
    manual_review_actions: list[CorporateAction] = field(default_factory=list)
    counts: CorporateActionIngestionCounts = field(default_factory=CorporateActionIngestionCounts)


class CorporateActionIngestionService:
    """Fetch, normalize, validate and store corporate actions.

    Storage only: applying an action to positions, sleeves and cash happens in the
    trading cycle and the research engine through the shared accounting rule;
    strategy history is split-adjusted on read.
    """

    def __init__(
        self,
        *,
        session: Session,
        run_id: str,
        audit_logger: Any,
        cycle_timestamp: datetime,
        fetch_start: str | None = None,
        fetch_end: str | None = None,
        fetch_symbols: list[str] | None = None,
    ) -> None:
        self.session = session
        self.normalization_service = CorporateActionNormalizationService()
        self.validation_service = CorporateActionValidationService()
        self.run_id = run_id
        self.cycle_timestamp = cycle_timestamp
        self.audit_logger = audit_logger
        self.fetch_start = fetch_start
        self.fetch_end = fetch_end
        self.fetch_symbols = fetch_symbols

    def ingest_corporate_actions(self) -> CorporateActionProcessingResult:
        component = "ingestion.corporate_action_ingestion_service"

        record_operation_started(
            logger=logger,
            event="corporate_action_ingestion_service_started",
            run_id=self.run_id,
            component=component,
            cycle_timestamp=self.cycle_timestamp.isoformat(),
            fetch_start=self.fetch_start,
            fetch_end=self.fetch_end,
            fetch_symbol_count=len(self.fetch_symbols) if self.fetch_symbols else None,
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

                _kwargs: dict = {}
                if self.fetch_start is not None:
                    _kwargs["start"] = self.fetch_start
                if self.fetch_end is not None:
                    _kwargs["end"] = self.fetch_end
                if self.fetch_symbols is not None:
                    _kwargs["symbols"] = self.fetch_symbols
                payload: dict = client.fetch_corporate_actions(**_kwargs)

            request_duration = perf_counter() - request_start
            corporate_action_request_latency_seconds.record(
                request_duration,
                {"component": component, "status": "completed"},
            )
        except Exception:
            request_duration = perf_counter() - request_start
            corporate_action_request_latency_seconds.record(
                request_duration,
                {"component": component, "status": "failed"},
            )
            raise

        counts = CorporateActionIngestionCounts()
        actions_block = payload.get("corporate_actions", {}) or {}

        # (provider_type, raw item) pairs; the Alpaca list key is the only type signal.
        tagged_actions: list[tuple[str, dict]] = []
        for list_key, items in actions_block.items():
            if not isinstance(items, list):
                continue
            provider_type = ALPACA_LIST_KEY_TO_PROVIDER_TYPE.get(str(list_key))
            if provider_type is None:
                counts.unsupported += len(items)
                if items:
                    counts.unsupported_list_keys.append(str(list_key))
                continue
            tagged_actions.extend((provider_type, item) for item in items)
        counts.fetched = counts.unsupported + len(tagged_actions)

        if counts.unsupported_list_keys:
            logger.info(
                "corporate_action_ingestion.unsupported_types_skipped",
                extra={
                    "run_id": self.run_id,
                    "list_keys": counts.unsupported_list_keys,
                    "count": counts.unsupported,
                },
            )

        corporate_action_ingestion_batch_size.record(
            len(tagged_actions),
            {"component": component},
        )

        record_operation_completed(
            logger=logger,
            event="corporate_action_ingestion_service_fetch_completed",
            run_id=self.run_id,
            component=component,
            raw_action_count=len(tagged_actions),
            unsupported_action_count=counts.unsupported,
            request_duration=request_duration,
        )

        with start_span(
            "corporate_action_ingestion_service.ingest", timespan=SpanTimespan.STEP
        ) as service_span:
            service_span.set_attribute("ratp.run_id", self.run_id)
            service_span.set_attribute("ratp.component", component)
            service_span.set_attribute("ratp.raw_action_count", len(tagged_actions))

            created_actions: list[CorporateAction] = []
            updated_actions: list[CorporateAction] = []
            manual_review_actions: list[CorporateAction] = []

            with SorUnitOfWork(self.session) as uow:
                for provider_type, raw_action in tagged_actions:
                    symbol = self._raw_symbol(raw_action)
                    processing_start = perf_counter()

                    corporate_action_records_processed.add(
                        1,
                        {"component": component, "symbol": symbol},
                    )

                    with start_span(
                        "corporate_action_ingestion_service.normalize_action",
                        timespan=SpanTimespan.STEP,
                    ) as normalization_span:
                        normalization_span.set_attribute("ratp.run_id", self.run_id)
                        normalization_span.set_attribute("ratp.symbol", symbol)
                        normalization_span.set_attribute("ratp.provider_type", provider_type)
                        try:
                            action = self.normalization_service.parse_alpaca_corporate_action(
                                raw_action,
                                provider_type,
                            )
                            normalization_span.set_attribute("ratp.normalization.failed", False)
                            normalization_span.set_attribute(
                                "ratp.action_type", action.action_type.value
                            )
                            normalization_span.set_attribute(
                                "ratp.effective_date",
                                action.effective_date.isoformat(),
                            )
                        except ValueError as exc:
                            normalization_span.set_attribute("ratp.normalization.failed", True)
                            counts.parse_failed += 1
                            corporate_action_normalization_failures.add(
                                1,
                                {"component": component, "symbol": symbol},
                            )
                            logger.warning(
                                "corporate_action_ingestion.parse_failed",
                                extra={
                                    "run_id": self.run_id,
                                    "symbol": symbol,
                                    "provider_type": provider_type,
                                    "error": str(exc),
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
                        {"component": component, "symbol": action.symbol},
                    )

                    with start_span(
                        "corporate_action_ingestion_service.validate_action",
                        timespan=SpanTimespan.STEP,
                    ) as validation_span:
                        validation_span.set_attribute("ratp.run_id", self.run_id)
                        validation_span.set_attribute("ratp.symbol", action.symbol)
                        validation_result = self.validation_service.validate(action)
                        if not validation_result.ok:
                            counts.validation_failed += 1
                            corporate_action_validation_failures.add(
                                1,
                                {"component": component, "symbol": action.symbol},
                            )
                            logger.warning(
                                "corporate_action_ingestion.validation_failed",
                                extra={
                                    "run_id": self.run_id,
                                    "symbol": action.symbol,
                                    "action_type": action.action_type.value,
                                    "violations": [
                                        getattr(v, "code", str(v))
                                        for v in validation_result.violations
                                    ],
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

                    corporate_action_processing_duration_seconds.record(
                        perf_counter() - processing_start,
                        {"component": component, "symbol": action.symbol},
                    )

                    if not result.created:
                        counts.updated += 1
                        updated_actions.append(action)
                        continue

                    counts.created += 1
                    created_actions.append(action)

                    corporate_actions_ingested.add(
                        1,
                        {
                            "component": component,
                            "symbol": action.symbol,
                            "action_type": action.action_type.value,
                        },
                    )

                    if action.action_type not in APPLIED_ACTION_TYPES:
                        counts.manual_review += 1
                        manual_review_actions.append(action)
                        logger.warning(
                            "corporate_action_ingestion.manual_review_required",
                            extra={
                                "run_id": self.run_id,
                                "symbol": action.symbol,
                                "action_type": action.action_type.value,
                                "effective_date": action.effective_date.isoformat(),
                                "new_symbol": action.new_symbol,
                            },
                        )
                        self.audit_logger.record_corporate_action_manual_review_required(
                            run_id=self.run_id,
                            symbol=action.symbol,
                            action_type=action.action_type.value,
                            effective_date=action.effective_date,
                            cycle_timestamp=self.cycle_timestamp,
                        )

        record_operation_completed(
            logger=logger,
            event="corporate_action_ingestion_service_completed",
            run_id=self.run_id,
            component=component,
            **{f"actions_{key}": value for key, value in counts.as_dict().items()},
        )
        return CorporateActionProcessingResult(
            created_actions=created_actions,
            updated_actions=updated_actions,
            manual_review_actions=manual_review_actions,
            counts=counts,
        )

    @staticmethod
    def _raw_symbol(raw_action: dict) -> str:
        for key in ("symbol", "acquiree_symbol", "source_symbol", "old_symbol"):
            value = raw_action.get(key)
            if isinstance(value, str) and value.strip():
                return value.upper()
        return "UNKNOWN"
