from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import features
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)


def _extract_json(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output[output.index("{") :]))


def _session_factory(db_session: Session):
    return lambda: db_session


def _seed_source_dataset(db_session: Session) -> None:
    db_session.add(
        DatasetVersions(
            dataset_version_id="raw-bars-v1",
            dataset_name="raw_bars",
            created_at=datetime(2026, 5, 8, 12, tzinfo=UTC),
            source="test",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="1.0.0",
            symbol_coverage=2,
            date_coverage_start=date(2026, 1, 1),
            date_coverage_end=date(2026, 5, 1),
            validation_status="validated",
            checksum="source-checksum",
            source_dataset_version=None,
            source_manifest={"symbols": ["AAPL", "MSFT"]},
            metadata_json={},
        )
    )


def _seed_feature_dataset(
    db_session: Session,
    *,
    feature_dataset_version_id: str = "feat-returns-v1",
    feature_name: str = "returns",
    storage_path: str = "features/returns/dataset_version=feat-returns-v1",
) -> None:
    db_session.add(
        FeatureDatasetVersions(
            dataset_version_id=feature_dataset_version_id,
            feature_name=feature_name,
            dataset_name="features",
            created_at=datetime(2026, 5, 8, 13, tzinfo=UTC),
            schema_version="1.0",
            source_dataset_version="raw-bars-v1",
            underlying_price_basis=PriceBasis.RAW,
            computation_parameters={"price_column": "close", "horizons": [1, 5, 20]},
            storage_path=storage_path,
            symbol_coverage=2,
            date_coverage_start=date(2026, 1, 1),
            date_coverage_end=date(2026, 5, 1),
            validation_status="validated",
            checksum="feature-checksum",
            source_manifest={"source_dataset_version": "raw-bars-v1"},
            metadata_json={"price_basis": "raw"},
            computation_code_version="test",
        )
    )


def test_features_commands_are_registered() -> None:
    parser = build_parser()

    command_args = [
        ["features", "list-datasets"],
        ["features", "inspect-dataset", "--feature-dataset-version-id", "feat-1"],
        ["features", "latest", "--feature-name", "returns", "--price-basis", "RAW"],
        ["features", "validate-dataset", "--feature-dataset-version-id", "feat-1"],
        [
            "features",
            "plan-pipeline",
            "--dataset-version-id",
            "raw-bars-v1",
            "--symbols",
            "AAPL,MSFT",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-05-01",
        ],
        [
            "features",
            "run-pipeline",
            "--dataset-version-id",
            "raw-bars-v1",
            "--symbols",
            "AAPL",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-05-01",
            "--dry-run",
            "--no-include-volatility",
        ],
        [
            "features",
            "register-dataset-version",
            "--feature-name",
            "returns",
            "--source-dataset-version",
            "raw-bars-v1",
            "--computation-parameters-json",
            '{"price_column":"close"}',
            "--storage-path",
            "features/returns/dataset_version=feat",
        ],
        ["features", "sample-dataset", "--feature-dataset-version-id", "feat-1"],
        [
            "features",
            "export-lineage",
            "--feature-dataset-version-id",
            "feat-1",
            "--output",
            "artifacts/features/lineage.json",
        ],
        [
            "features",
            "resolve-for-simulation",
            "--strategy-type",
            "momentum",
            "--source-dataset-version",
            "raw-bars-v1",
            "--symbols",
            "AAPL,MSFT",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-05-01",
        ],
    ]

    for argv in command_args:
        args = parser.parse_args(argv)
        assert args.domain == "features"
        assert hasattr(args, "func")


def test_run_pipeline_boolean_optional_flags_disable_jobs() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "features",
            "run-pipeline",
            "--dataset-version-id",
            "raw-bars-v1",
            "--symbols",
            "AAPL",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-05-01",
            "--no-include-volatility",
            "--no-include-moving-average",
        ]
    )

    assert args.include_returns is True
    assert args.include_volatility is False
    assert args.include_moving_average is False


def test_plan_pipeline_reuses_existing_feature_dataset(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _seed_source_dataset(db_session)
    _seed_feature_dataset(db_session)
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(features, "get_session", _session_factory(db_session))

    args = argparse.Namespace(
        dataset_version_id="raw-bars-v1",
        price_basis="RAW",
        symbols="AAPL,MSFT",
        universe_version_id=None,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 1),
        include_returns=True,
        include_volatility=False,
        include_moving_average=False,
        include_liquidity=False,
        include_regime=False,
        include_regime_classification=False,
        output=None,
    )

    assert features.handle_plan_pipeline(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["dry_run"] is True
    assert payload["feature_steps"][0]["action"] == "reuse"
    assert payload["feature_steps"][0]["existing_feature_dataset_version_id"] == "feat-returns-v1"


def test_run_pipeline_dry_run_does_not_execute_cycle(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _seed_source_dataset(db_session)
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(features, "get_session", _session_factory(db_session))
    monkeypatch.setattr(
        features,
        "run_feature_pipeline_cycle",
        lambda **_: pytest.fail("dry-run must not execute the feature pipeline cycle"),
    )

    args = argparse.Namespace(
        dataset_version_id="raw-bars-v1",
        price_basis="RAW",
        symbols="AAPL",
        universe_version_id=None,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 1),
        include_returns=True,
        include_volatility=False,
        include_moving_average=False,
        include_liquidity=False,
        include_regime=False,
        include_regime_classification=False,
        dry_run=True,
        output=None,
    )

    assert features.handle_run_pipeline(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["would_write"] is False
    assert payload["feature_steps"][0]["feature_name"] == "returns"


def test_run_pipeline_invokes_cycle_with_validated_inputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    captured: dict[str, Any] = {}

    def fake_cycle(**kwargs):
        captured.update(kwargs)
        return {
            "run_id": "run-id",
            "runtime_job_run_id": "job-run-id",
            "dataset_version_id": kwargs["dataset_version_id"],
            "feature_dataset_versions": [],
        }

    monkeypatch.setattr(features, "setup_telemetry", lambda *_: None)
    monkeypatch.setattr(features, "run_feature_pipeline_cycle", fake_cycle)

    args = argparse.Namespace(
        dataset_version_id="raw-bars-v1",
        price_basis="RAW",
        symbols="aapl",
        universe_version_id="universe-v1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 1),
        include_returns=True,
        include_volatility=False,
        include_moving_average=False,
        include_liquidity=False,
        include_regime=False,
        include_regime_classification=False,
        dry_run=False,
        output=None,
    )

    assert features.handle_run_pipeline(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert captured["symbols"] == ["AAPL"]
    assert captured["universe_version_id"] == "universe-v1"
    assert captured["include_volatility"] is False
    assert payload["runtime_job_run_id"] == "job-run-id"


def test_read_only_feature_dataset_commands(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _seed_source_dataset(db_session)
    _seed_feature_dataset(db_session)
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(features, "get_session", _session_factory(db_session))

    commands = [
        (
            features.handle_list_datasets,
            argparse.Namespace(
                feature_name="returns",
                source_dataset_version="raw-bars-v1",
                validated_only=True,
                limit=20,
                price_basis=None,
                output=None,
            ),
            "feature_dataset_versions",
        ),
        (
            features.handle_inspect_dataset,
            argparse.Namespace(feature_dataset_version_id="feat-returns-v1", output=None),
            "feature_dataset_version",
        ),
        (
            features.handle_latest,
            argparse.Namespace(feature_name="returns", price_basis="RAW", output=None),
            "feature_dataset_version",
        ),
        (
            features.handle_validate_dataset,
            argparse.Namespace(
                feature_dataset_version_id="feat-returns-v1",
                check_parquet=False,
                output=None,
            ),
            "ok",
        ),
    ]

    for handler, args, expected_key in commands:
        assert handler(args) == 0
        payload = _extract_json(capsys.readouterr().out)
        assert expected_key in payload


def test_register_dataset_version_creates_feature_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(features, "get_session", _session_factory(db_session))

    args = argparse.Namespace(
        dataset_version_id="feat-cli-v1",
        feature_name="returns",
        dataset_name="features",
        source_dataset_version="raw-bars-v1",
        price_basis="RAW",
        schema_version="1.0",
        validation_status="unvalidated",
        computation_parameters_json='{"price_column":"close"}',
        computation_code_version="test",
        storage_path="features/returns/dataset_version=feat-cli-v1",
        symbol_coverage=1,
        date_coverage_start=date(2026, 1, 1),
        date_coverage_end=date(2026, 5, 1),
        checksum=None,
        source_manifest_json=None,
        metadata_json=None,
        output=None,
    )

    assert features.handle_register_dataset_version(args) == 0
    payload = _extract_json(capsys.readouterr().out)

    assert payload["registered"] is True
    assert db_session.get(FeatureDatasetVersions, "feat-cli-v1") is not None


def test_export_lineage_writes_json_file(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    _seed_source_dataset(db_session)
    _seed_feature_dataset(db_session)
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(features, "get_session", _session_factory(db_session))
    output_path = tmp_path / "lineage.json"

    assert (
        features.handle_export_lineage(
            argparse.Namespace(
                feature_dataset_version_id="feat-returns-v1",
                output=str(output_path),
            )
        )
        == 0
    )
    capsys.readouterr()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["feature_dataset_version"]["feature_dataset_version_id"] == "feat-returns-v1"
    assert payload["source_dataset"]["dataset_version_id"] == "raw-bars-v1"


def test_resolve_for_simulation_reports_missing_and_resolved_dependencies(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _seed_source_dataset(db_session)
    _seed_feature_dataset(db_session)
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(features, "get_session", _session_factory(db_session))

    assert (
        features.handle_resolve_for_simulation(
            argparse.Namespace(
                strategy_type="momentum",
                features=None,
                source_dataset_version="raw-bars-v1",
                price_basis="RAW",
                symbols="AAPL,MSFT",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 5, 1),
                output=None,
            )
        )
        == 0
    )
    payload = _extract_json(capsys.readouterr().out)

    assert payload["all_resolved"] is False
    assert payload["dependencies"][0]["status"] == "resolved"
    assert payload["dependencies"][1]["status"] == "missing"


def test_sample_dataset_reads_small_parquet_sample(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    sample_root = tmp_path / "feature_sample"
    sample_root.mkdir()
    pq.write_table(
        pa.table(
            {
                "timestamp": [datetime(2026, 1, 1, tzinfo=UTC)],
                "symbol": ["AAPL"],
                "ret_1d": [0.01],
            }
        ),
        sample_root / "data.parquet",
    )
    _seed_feature_dataset(db_session, storage_path=str(sample_root))
    db_session.flush()
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(features, "get_session", _session_factory(db_session))

    assert (
        features.handle_sample_dataset(
            argparse.Namespace(
                feature_dataset_version_id="feat-returns-v1",
                symbol="AAPL",
                limit=5,
                output=None,
            )
        )
        == 0
    )
    payload = _extract_json(capsys.readouterr().out)

    assert payload["row_count"] == 1
    assert payload["rows"][0]["symbol"] == "AAPL"
