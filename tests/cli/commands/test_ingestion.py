from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import ingestion
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    CheckpointScope,
    CheckpointStatus,
    CorporateActionType,
    MarketSession,
    PriceBasis,
    RunType,
)
from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.ingestion_checkpoint import (
    IngestionCheckpoint,
)
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.missing_bar_incidents import (
    MissingBarIncidents,
)
from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import (
    SymbolDateCoverage,
)


def _extract_json(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output[output.index("{") :]))


def _session_factory(db_session: Session):
    return lambda: db_session


def test_new_ingestion_commands_are_registered() -> None:
    parser = build_parser()

    command_args = [
        ["ingestion", "plan-bars"],
        ["ingestion", "run-bars", "--dry-run", "--symbols", "SPY,AAPL"],
        [
            "ingestion",
            "run-backfill",
            "--symbols",
            "SPY",
            "--start",
            "2026-05-01T13:30:00Z",
            "--end",
            "2026-05-01T20:00:00Z",
            "--dry-run",
        ],
        ["ingestion", "run-corporate-actions", "--dry-run"],
        ["ingestion", "inspect-ingestion-run", "--ingestion-run-id", "run-1"],
        ["ingestion", "list-ingestion-runs"],
        ["ingestion", "inspect-dataset-version", "--dataset-version-id", "dataset-1"],
        ["ingestion", "latest-dataset", "--dataset-name", "raw_bars"],
        ["ingestion", "inspect-checkpoint", "--ingestion-run-id", "run-1"],
        ["ingestion", "list-incidents", "--dataset-version", "dataset-1"],
        ["ingestion", "inspect-coverage", "--dataset-version", "dataset-1", "--symbol", "SPY"],
        ["ingestion", "inspect-corporate-action", "--symbol", "SPY"],
    ]

    for argv in command_args:
        args = parser.parse_args(argv)
        assert args.domain == "ingestion"
        assert hasattr(args, "func")


def test_run_bars_dry_run_uses_symbol_override_without_cycle(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(ingestion, "get_session", _session_factory(db_session))
    monkeypatch.setattr(
        ingestion,
        "run_market_ingestion_cycle",
        lambda **_: pytest.fail("dry-run must not execute the live cycle"),
    )

    args = argparse.Namespace(
        timestamp="2026-05-08T12:07:00Z",
        symbols="spy,AAPL",
        dry_run=True,
        actor="operator@example.com",
    )

    assert ingestion.handle_run_bars(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["dry_run"] is True
    assert payload["would_fetch_external_data"] is False
    assert payload["would_write"] is False
    assert payload["cycle_start"] == "2026-05-08T12:00:00+00:00"
    assert payload["cycle_end"] == "2026-05-08T12:05:00+00:00"
    assert payload["symbols"] == ["SPY", "AAPL"]
    assert payload["actor"] == "operator@example.com"


def test_run_bars_passes_symbols_and_actor_to_cycle(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    captured: dict[str, Any] = {}

    def fake_cycle(**kwargs):
        captured.update(kwargs)
        return {
            "run_id": "run-id",
            "runtime_job_run_id": "job-run-id",
            "ingestion_run_id": "ingestion-run-id",
            "dataset_version_id": "dataset-version-id",
        }

    monkeypatch.setattr(ingestion, "run_market_ingestion_cycle", fake_cycle)

    args = argparse.Namespace(
        timestamp="2026-05-08T12:07:00Z",
        symbols="SPY,AAPL",
        dry_run=False,
        actor="operator@example.com",
    )

    assert ingestion.handle_run_bars(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert captured["symbols_override"] == ["SPY", "AAPL"]
    assert captured["trigger_type"] == "manual_cli"
    assert captured["actor"] == "operator@example.com"
    assert payload["ingestion_run_id"] == "ingestion-run-id"


def test_run_backfill_validates_date_order() -> None:
    args = argparse.Namespace(
        symbols="SPY",
        start="2026-05-02T20:00:00Z",
        end="2026-05-01T13:30:00Z",
        dry_run=False,
        max_days=31,
        max_symbols=50,
        actor=None,
    )

    with pytest.raises(ValueError, match="--end must be greater"):
        ingestion.handle_run_backfill(args)


def test_run_backfill_rejects_duplicate_symbols() -> None:
    args = argparse.Namespace(
        symbols="SPY,spy",
        start="2026-05-01T13:30:00Z",
        end="2026-05-01T20:00:00Z",
        dry_run=False,
        max_days=31,
        max_symbols=50,
        actor=None,
    )

    with pytest.raises(ValueError, match="Duplicate symbols"):
        ingestion.handle_run_backfill(args)


def test_run_backfill_dry_run_does_not_execute_cycle(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        ingestion,
        "run_market_backfill_cycle",
        lambda **_: pytest.fail("dry-run must not execute the live cycle"),
    )
    args = argparse.Namespace(
        symbols="SPY,AAPL",
        start="2026-05-01T13:30:00Z",
        end="2026-05-02T20:00:00Z",
        dry_run=True,
        max_days=31,
        max_symbols=50,
        actor="operator",
    )

    assert ingestion.handle_run_backfill(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["dry_run"] is True
    assert payload["would_fetch_external_data"] is False
    assert payload["estimated_symbol_days"] == 4
    assert payload["actor"] == "operator"


def test_run_corporate_actions_dry_run_resolves_latest_raw_dataset(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    db_session.add(
        DatasetVersions(
            dataset_version_id="raw-bars-v1",
            dataset_name="raw_bars",
            created_at=now,
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="1.0.0",
            symbol_coverage=1,
            date_coverage_start=now.date(),
            date_coverage_end=now.date(),
            validation_status="validated",
            checksum=None,
            source_dataset_version=None,
            source_manifest={},
            metadata_json={},
        )
    )
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(ingestion, "get_session", _session_factory(db_session))

    args = argparse.Namespace(
        dry_run=True,
        source_raw_bars_dataset_version=None,
        actor="operator",
    )

    assert ingestion.handle_run_corporate_actions(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["dry_run"] is True
    assert payload["source_raw_bars_dataset_version"]["dataset_version_id"] == "raw-bars-v1"


def test_inspect_bar_does_not_commit_read_only_session(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    timestamp = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    db_session.add(
        MarketBar(
            bar_id="bar-1",
            timestamp=timestamp,
            end_timestamp=timestamp + timedelta(minutes=5),
            interval=BarInterval.FIVE_MIN,
            symbol="SPY",
            open=Decimal("100.00"),
            high=Decimal("101.00"),
            low=Decimal("99.00"),
            close=Decimal("100.50"),
            volume=1000,
            vwap=Decimal("100.25"),
            trade_count=10,
            price_basis=PriceBasis.RAW,
            adjustment_factor=1.0,
            source="alpaca",
            ingested_at=timestamp,
            quality_flags=[],
            session=MarketSession.REGULAR,
        )
    )
    db_session.flush()
    monkeypatch.setattr(ingestion, "get_session", _session_factory(db_session))
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: pytest.fail("inspect-bar must not commit"),
    )

    args = argparse.Namespace(symbol="SPY", timestamp="2026-05-08T12:00:00Z")

    assert ingestion.handle_inspect_bar(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["found"] is True
    assert payload["bar"]["bar_id"] == "bar-1"


def test_read_only_inspection_commands_return_rows(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    db_session.add_all(
        [
            DatasetVersions(
                dataset_version_id="dataset-1",
                dataset_name="raw_bars",
                created_at=now,
                source="alpaca",
                price_basis=PriceBasis.RAW,
                interval=BarInterval.FIVE_MIN,
                schema_version="1.0.0",
                symbol_coverage=1,
                date_coverage_start=now.date(),
                date_coverage_end=now.date(),
                validation_status="validated",
                checksum="checksum",
                source_dataset_version=None,
                source_manifest={},
                metadata_json={},
            ),
            IngestionRuns(
                ingestion_run_id="ingestion-run-1",
                created_at=now,
                run_timestamp=now,
                run_type=RunType.INGESTION,
                source="alpaca",
                dataset_version="dataset-1",
                status="completed",
                started_at=now,
                completed_at=now + timedelta(minutes=1),
                error_message=None,
                row_count=1,
                file_count=1,
            ),
            IngestionCheckpoint(
                checkpoint_id="checkpoint-1",
                ingestion_run_id="ingestion-run-1",
                dataset_version="dataset-1",
                checkpoint_scope=CheckpointScope.INCREMENTAL,
                checkpoint_status=CheckpointStatus.COMPLETED,
                symbol="SPY",
                checkpoint_date=now.date(),
                cycle_timestamp=now,
                last_successful_bar_timestamp=now,
                retry_count=0,
                error_message=None,
                created_at=now,
                updated_at=now,
            ),
            MissingBarIncidents(
                incident_id="incident-1",
                symbol="SPY",
                bar_timestamp=now,
                dataset_version="dataset-1",
                run_id="run-1",
                ingestion_run_id="ingestion-run-1",
                incident_type="missing",
                severity="warning",
                message="missing bar",
                created_at=now,
                resolved_flag=False,
            ),
            SymbolDateCoverage(
                coverage_id="coverage-1",
                symbol="SPY",
                date=date(2026, 5, 8),
                dataset_version="dataset-1",
                expected_bar_count=78,
                actual_bar_count=78,
                completeness_status="complete",
                gap_summary=None,
                updated_at=now,
            ),
            CorporateAction(
                action_id="action-1",
                symbol="SPY",
                action_type=CorporateActionType.SPLIT_FORWARD,
                effective_date=date(2026, 5, 8),
                announced_date=None,
                record_date=None,
                payable_date=None,
                split_ratio=2.0,
                cash_amount=None,
                currency="USD",
                new_symbol="SPY",
                source="alpaca",
                ingested_at=now,
                meta={},
            ),
        ]
    )
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(ingestion, "get_session", _session_factory(db_session))

    commands = [
        (
            ingestion.handle_inspect_ingestion_run,
            argparse.Namespace(ingestion_run_id="ingestion-run-1"),
            "ingestion_run",
        ),
        (
            ingestion.handle_list_ingestion_runs,
            argparse.Namespace(limit=20, status=None),
            "ingestion_runs",
        ),
        (
            ingestion.handle_inspect_dataset_version,
            argparse.Namespace(dataset_version_id="dataset-1"),
            "dataset_version",
        ),
        (
            ingestion.handle_latest_dataset,
            argparse.Namespace(dataset_name="raw_bars", price_basis=PriceBasis.RAW),
            "dataset_version",
        ),
        (
            ingestion.handle_inspect_checkpoint,
            argparse.Namespace(ingestion_run_id="ingestion-run-1", symbol="SPY"),
            "checkpoints",
        ),
        (
            ingestion.handle_list_incidents,
            argparse.Namespace(dataset_version="dataset-1", symbol="SPY", limit=100),
            "incidents",
        ),
        (
            ingestion.handle_inspect_coverage,
            argparse.Namespace(dataset_version="dataset-1", symbol="SPY"),
            "coverage",
        ),
        (
            ingestion.handle_inspect_corporate_action,
            argparse.Namespace(symbol="SPY", limit=50),
            "corporate_actions",
        ),
    ]

    for handler, args, expected_key in commands:
        assert handler(args) == 0
        payload = _extract_json(capsys.readouterr().out)
        assert expected_key in payload
