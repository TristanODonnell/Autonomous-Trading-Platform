from __future__ import annotations

from autonomous_trading_platform.contracts.runtime.ingestion_run import IngestionRun

from .core import Rule, is_non_negative

INGESTION_RUN_RULES: list[Rule[IngestionRun]] = [
    Rule(
        code="INGESTION_RUN_ID_NON_EMPTY",
        field="ingestion_run_id",
        check=lambda ir, _ctx: (
            isinstance(ir.ingestion_run_id, str) and ir.ingestion_run_id.strip() != ""
        ),
        message=lambda ir, _ctx: "ingestion_run_id must be a non-empty string",
    ),
    Rule(
        code="SOURCE_NON_EMPTY",
        field="source",
        check=lambda ir, _ctx: isinstance(ir.source, str) and ir.source.strip() != "",
        message=lambda ir, _ctx: "source must be a non-empty string",
    ),
    Rule(
        code="DATASET_VERSION_NON_EMPTY",
        field="dataset_version",
        check=lambda ir, _ctx: (
            isinstance(ir.dataset_version, str) and ir.dataset_version.strip() != ""
        ),
        message=lambda ir, _ctx: "dataset_version must be a non-empty string",
    ),
    Rule(
        code="STATUS_NON_EMPTY",
        field="status",
        check=lambda ir, _ctx: isinstance(ir.status, str) and ir.status.strip() != "",
        message=lambda ir, _ctx: "status must be a non-empty string",
    ),
    Rule(
        code="STARTED_AT_GE_CREATED_AT",
        field="started_at",
        check=lambda ir, _ctx: ir.started_at >= ir.created_at,
        message=lambda ir, _ctx: "started_at must be >= created_at",
    ),
    Rule(
        code="COMPLETED_AT_GE_STARTED_AT_WHEN_PRESENT",
        field="completed_at",
        check=lambda ir, _ctx: ir.completed_at is None or ir.completed_at >= ir.started_at,
        message=lambda ir, _ctx: "completed_at must be >= started_at when present",
    ),
    Rule(
        code="ROW_COUNT_NON_NEGATIVE_WHEN_PRESENT",
        field="row_count",
        check=lambda ir, _ctx: ir.row_count is None or is_non_negative(ir.row_count),
        message=lambda ir, _ctx: "row_count must be >= 0 when present",
    ),
    Rule(
        code="FILE_COUNT_NON_NEGATIVE_WHEN_PRESENT",
        field="file_count",
        check=lambda ir, _ctx: ir.file_count is None or is_non_negative(ir.file_count),
        message=lambda ir, _ctx: "file_count must be >= 0 when present",
    ),
    Rule(
        code="COMPLETED_STATUS_REQUIRES_COMPLETED_AT",
        field="completed_at",
        check=lambda ir, _ctx: (
            ir.status.lower() not in {"completed", "succeeded", "failed"}
            or ir.completed_at is not None
        ),
        message=lambda ir, _ctx: (
            "completed_at is required when status is completed/succeeded/failed"
        ),
    ),
    Rule(
        code="FAILED_STATUS_REQUIRES_ERROR_MESSAGE",
        field="error_message",
        check=lambda ir, _ctx: (
            ir.status.lower() != "failed"
            or (ir.error_message is not None and ir.error_message.strip() != "")
        ),
        message=lambda ir, _ctx: "error_message is required when status is failed",
    ),
]
