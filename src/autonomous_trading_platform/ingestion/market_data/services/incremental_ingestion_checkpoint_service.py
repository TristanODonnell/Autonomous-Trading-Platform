from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    CheckpointScope,
    CheckpointStatus,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.storage.sor.models.ingestion_checkpoint import IngestionCheckpoint
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class IncrementalIngestionCheckpointService:
    """
    Manages lifecycle of incremental ingestion checkpoints (per cycle timestamp).
    """

    def __init__(
        self,
        session: Session,
        audit_logger: AuditLoggingService,
        *,
        component: str,
        max_retries: int = 3,
        timeout: timedelta = timedelta(minutes=15),
    ) -> None:
        self.session = session
        self.audit_logger = audit_logger
        self.component = component
        self.max_retries = max_retries
        self.timeout = timeout

    def get_or_create_checkpoint(
        self,
        *,
        ingestion_run_id: str,
        dataset_version_id: str,
        cycle_timestamp: datetime,
    ) -> IngestionCheckpoint:
        checkpoint_id = f"{dataset_version_id}:{cycle_timestamp.isoformat()}:incremental"
        now = datetime.now(UTC)

        with SorUnitOfWork(self.session) as uow:
            checkpoint = uow.ingestion_checkpoints.get_cycle_checkpoint(
                ingestion_run_id=ingestion_run_id,
                dataset_version=dataset_version_id,
                cycle_timestamp=cycle_timestamp,
            )

            if checkpoint is None:
                checkpoint = IngestionCheckpoint(
                    checkpoint_id=checkpoint_id,
                    ingestion_run_id=ingestion_run_id,
                    dataset_version=dataset_version_id,
                    checkpoint_scope=CheckpointScope.INCREMENTAL,
                    checkpoint_status=CheckpointStatus.PENDING,
                    symbol=None,
                    checkpoint_date=None,
                    cycle_timestamp=cycle_timestamp,
                    last_successful_bar_timestamp=None,
                    retry_count=0,
                    error_message=None,
                    created_at=now,
                    updated_at=now,
                )
                uow.ingestion_checkpoints.insert(checkpoint)

                self._audit(
                    ingestion_run_id,
                    "incremental_checkpoint_created",
                    checkpoint,
                )

            return checkpoint

    def can_start_cycle(
        self,
        checkpoint: IngestionCheckpoint,
    ) -> tuple[bool, str]:
        now = datetime.now(UTC)

        if checkpoint.checkpoint_status == CheckpointStatus.COMPLETED:
            return False, "already_completed"

        if checkpoint.retry_count >= self.max_retries:
            self._audit(
                checkpoint.ingestion_run_id,
                "incremental_checkpoint_retry_limit_reached",
                checkpoint,
            )
            return False, "retry_limit_reached"

        if checkpoint.checkpoint_status == CheckpointStatus.IN_PROGRESS:
            if now <= checkpoint.updated_at + self.timeout:
                return False, "in_progress_not_timed_out"

            # stale reclaim
            self._audit(
                checkpoint.ingestion_run_id,
                "incremental_checkpoint_timeout_reclaimed",
                checkpoint,
            )
            return True, "stale_reclaimed"

        return True, "eligible"

    def mark_in_progress(self, checkpoint: IngestionCheckpoint) -> IngestionCheckpoint:
        checkpoint.checkpoint_status = CheckpointStatus.IN_PROGRESS
        checkpoint.error_message = None
        checkpoint.updated_at = datetime.now(UTC)

        with SorUnitOfWork(self.session) as uow:
            uow.ingestion_checkpoints.upsert(checkpoint)

        self._audit(
            checkpoint.ingestion_run_id,
            "incremental_checkpoint_in_progress",
            checkpoint,
        )

        return checkpoint

    def mark_completed(
        self,
        checkpoint: IngestionCheckpoint,
        *,
        last_successful_bar_timestamp: datetime | None = None,
    ) -> IngestionCheckpoint:
        checkpoint.checkpoint_status = CheckpointStatus.COMPLETED
        checkpoint.last_successful_bar_timestamp = last_successful_bar_timestamp
        checkpoint.error_message = None
        checkpoint.updated_at = datetime.now(UTC)

        with SorUnitOfWork(self.session) as uow:
            uow.ingestion_checkpoints.upsert(checkpoint)

        self._audit(
            checkpoint.ingestion_run_id,
            "incremental_checkpoint_completed",
            checkpoint,
        )

        return checkpoint

    def mark_failed(
        self,
        checkpoint: IngestionCheckpoint,
        exc: Exception,
    ) -> IngestionCheckpoint:
        checkpoint.checkpoint_status = CheckpointStatus.FAILED
        checkpoint.retry_count += 1
        checkpoint.error_message = str(exc)
        checkpoint.updated_at = datetime.now(UTC)

        with SorUnitOfWork(self.session) as uow:
            uow.ingestion_checkpoints.upsert(checkpoint)

        self._audit(
            checkpoint.ingestion_run_id,
            "incremental_checkpoint_failed",
            checkpoint,
        )

        return checkpoint

    def _audit(
        self,
        ingestion_run_id: str,
        event_type: str,
        checkpoint: IngestionCheckpoint,
    ) -> None:
        self.audit_logger._record_event(
            run_id=ingestion_run_id,
            event_type=event_type,
            component=self.component,
            message=event_type,
            metadata={
                "checkpoint_id": checkpoint.checkpoint_id,
                "cycle_timestamp": (
                    checkpoint.cycle_timestamp.isoformat() if checkpoint.cycle_timestamp else None
                ),
                "status": checkpoint.checkpoint_status.value,
                "retry_count": checkpoint.retry_count,
            },
        )
