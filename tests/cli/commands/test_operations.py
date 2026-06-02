from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import operations
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakCheckName,
    RuntimeSoakCheckResult,
    RuntimeSoakSeverity,
    RuntimeSoakStatus,
    RuntimeSoakVerificationReport,
)
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_run_manifest,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)


def _build_dependencies(
    db_session: Session,
    monkeypatch,
) -> operations.OperationsCliDependencies:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.setenv("NO_LIVE_TRADING", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")

    return operations.OperationsCliDependencies(
        session=db_session,
        settings=Settings(),
    )


def _extract_json(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output[output.index("{") :]))


def _seed_healthy_runtime_window(db_session: Session) -> None:
    window_start = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    window_end = datetime(2026, 5, 8, 12, 30, tzinfo=UTC)
    dataset_created_at = _sqlite_round_trip_utc(window_end - timedelta(minutes=5))

    for index, job_name in enumerate(
        (
            "market_ingestion_cycle",
            "feature_pipeline_cycle",
            "trading_cycle",
        )
    ):
        started_at = window_start + timedelta(minutes=5 + index)
        db_session.add(
            RuntimeJobRuns(
                job_run_id=str(uuid4()),
                job_name=job_name,
                parent_job_run_id=None,
                status="completed",
                trigger_type="scheduled",
                started_at=started_at,
                completed_at=started_at + timedelta(minutes=1),
                duration_ms=60_000,
                error_message=None,
                correlation_id=str(uuid4()),
                input_summary_json=None,
                output_summary_json=None,
            )
        )

    manifest = build_trading_run_manifest(
        run_id=uuid4(),
        now_utc=window_end - timedelta(minutes=1),
        cycle_start=window_end - timedelta(minutes=6),
        cycle_end=window_end - timedelta(minutes=1),
    )
    manifest.created_at = window_end - timedelta(minutes=1)
    manifest.status = "completed"
    manifest.current_step = "risk_snapshot"
    manifest.last_successful_step = "risk_snapshot"
    RunManifestRepository(db_session).add(manifest)
    db_session.add(
        DatasetVersions(
            dataset_version_id="raw-bars-v1",
            dataset_name="market_bars",
            created_at=dataset_created_at,
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="v1",
            symbol_coverage=1,
            date_coverage_start=window_end.date(),
            date_coverage_end=window_end.date(),
            validation_status="validated",
            checksum="checksum",
            source_dataset_version=None,
            source_manifest={},
            metadata_json=None,
        )
    )
    db_session.add(
        FeatureDatasetVersions(
            dataset_version_id="feature-v1",
            feature_name="returns",
            dataset_name="feature_dataset",
            created_at=dataset_created_at,
            schema_version="v1",
            source_dataset_version="raw-bars-v1",
            underlying_price_basis=PriceBasis.RAW,
            computation_parameters={},
            storage_path="/tmp/features",
            symbol_coverage=1,
            date_coverage_start=window_end.date(),
            date_coverage_end=window_end.date(),
            validation_status="validated",
            checksum="checksum",
            source_manifest={},
            metadata_json=None,
            computation_code_version="dev",
        )
    )
    db_session.flush()


def _sqlite_round_trip_utc(timestamp: datetime) -> datetime:
    local_offset = timestamp.astimezone().utcoffset() or timedelta(0)
    return timestamp + local_offset


def test_operations_verify_runtime_soak_command_is_registered() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "operations",
            "verify-runtime-soak",
            "--window-start",
            "2026-05-08T12:00:00Z",
            "--window-end",
            "2026-05-08T12:30:00Z",
        ]
    )

    assert args.domain == "operations"
    assert args.operations_command == "verify-runtime-soak"
    assert args.func is operations.handle_verify_runtime_soak


def test_verify_runtime_soak_prints_report_and_returns_zero_for_warning(
    db_session: Session,
    monkeypatch,
    capsys,
) -> None:
    _seed_healthy_runtime_window(db_session)
    monkeypatch.setattr(
        operations,
        "build_dependencies",
        lambda: _build_dependencies(db_session, monkeypatch),
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "operations",
            "verify-runtime-soak",
            "--window-start",
            "2026-05-08T12:00:00Z",
            "--window-end",
            "2026-05-08T12:30:00Z",
        ]
    )

    exit_code = args.func(args)
    payload = _extract_json(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == RuntimeSoakStatus.WARNING.value
    assert payload["environment"] == "paper"
    assert payload["window_start"] == "2026-05-08T12:00:00Z"
    assert payload["window_end"] == "2026-05-08T12:30:00Z"
    assert payload["summary"]["total_checks"] == len(payload["checks"])


def test_verify_runtime_soak_returns_one_for_failed_report(
    db_session: Session,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        operations,
        "build_dependencies",
        lambda: _build_dependencies(db_session, monkeypatch),
    )

    def _failed_report(self, *, window_start: datetime, window_end: datetime):
        return RuntimeSoakVerificationReport(
            status=RuntimeSoakStatus.FAILED,
            window_start=window_start,
            window_end=window_end,
            environment="paper",
            checks=[
                RuntimeSoakCheckResult(
                    check_name=RuntimeSoakCheckName.DATA_FRESHNESS,
                    status=RuntimeSoakStatus.FAILED,
                    severity=RuntimeSoakSeverity.ERROR,
                    message="Data freshness failed.",
                    metadata={},
                )
            ],
            summary={
                "total_checks": 1,
                "passed": 0,
                "warnings": 0,
                "failed": 1,
            },
            checked_at=datetime(2026, 5, 8, 12, 31, tzinfo=UTC),
        )

    monkeypatch.setattr(
        operations.RuntimeSoakVerificationService,
        "verify",
        _failed_report,
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "operations",
            "verify-runtime-soak",
            "--window-start",
            "2026-05-08T12:00:00Z",
            "--window-end",
            "2026-05-08T12:30:00Z",
        ]
    )

    exit_code = args.func(args)
    payload = _extract_json(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == RuntimeSoakStatus.FAILED.value
    assert payload["summary"]["failed"] == 1


# ---------------------------------------------------------------------------
# inspect-ingestion-readiness
# ---------------------------------------------------------------------------


class TestInspectIngestionReadiness:
    def test_registered(self):
        parser = build_parser()
        args = parser.parse_args(["operations", "inspect-ingestion-readiness"])
        assert args.func is operations.handle_inspect_ingestion_readiness

    def test_timestamp_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            ["operations", "inspect-ingestion-readiness", "--timestamp", "2026-05-26T15:35:00Z"]
        )
        assert args.timestamp == "2026-05-26T15:35:00Z"

    def test_returns_zero(self, monkeypatch, capsys):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            operations,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(ready=True, safe_mode=False, reason="ok"),
        )
        args = argparse.Namespace(
            timestamp=None, func=operations.handle_inspect_ingestion_readiness
        )
        assert operations.handle_inspect_ingestion_readiness(args) == 0

    def test_output_contains_ingestion_ready_key(self, monkeypatch, capsys):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            operations,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(ready=True, safe_mode=False, reason=None),
        )
        args = argparse.Namespace(
            timestamp=None, func=operations.handle_inspect_ingestion_readiness
        )
        operations.handle_inspect_ingestion_readiness(args)
        payload = _extract_json(capsys.readouterr().out)
        assert "ingestion_ready" in payload
        assert payload["ingestion_ready"] is True

    def test_output_no_domain_note(self, monkeypatch, capsys):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            operations,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(ready=False, safe_mode=True, reason="late"),
        )
        args = argparse.Namespace(
            timestamp=None, func=operations.handle_inspect_ingestion_readiness
        )
        operations.handle_inspect_ingestion_readiness(args)
        payload = _extract_json(capsys.readouterr().out)
        assert "domain_note" not in payload

    def test_safe_mode_propagated(self, monkeypatch, capsys):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            operations,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(
                ready=False, safe_mode=True, reason="deadline passed"
            ),
        )
        args = argparse.Namespace(
            timestamp=None, func=operations.handle_inspect_ingestion_readiness
        )
        operations.handle_inspect_ingestion_readiness(args)
        payload = _extract_json(capsys.readouterr().out)
        assert payload["safe_mode"] is True
        assert payload["ingestion_ready"] is False
