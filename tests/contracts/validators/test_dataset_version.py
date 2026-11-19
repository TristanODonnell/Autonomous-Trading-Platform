"""Which dataset names and schema versions a ``dataset_versions`` row may carry."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.dataset_version import (
    DATASET_VERSION_RULES,
    SUPPORTED_SCHEMA_VERSIONS,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
    CORPORATE_ACTIONS_DATASET,
    RAW_BARS_DATASET,
    UNIVERSE_TIMELINE_DATASET,
)


def _version(**overrides: Any) -> DatasetVersion:
    data: dict[str, Any] = {
        "dataset_version_id": "dv-001",
        "dataset_name": RAW_BARS_DATASET.dataset_key,
        "created_at": datetime(2026, 11, 19, tzinfo=UTC),
        "source": "test",
        "price_basis": PriceBasis.RAW,
        "interval": BarInterval.FIVE_MIN,
        "schema_version": RAW_BARS_DATASET.schema_version,
        "symbol_coverage": 2,
        "date_coverage_start": date(2024, 11, 18),
        "date_coverage_end": date(2024, 11, 29),
        "validation_status": "validated",
        "checksum": "abc",
        "source_manifest": {"pipeline": "test"},
    }
    data.update(overrides)
    return DatasetVersion(**data)


def _codes(version: DatasetVersion) -> list[str]:
    return [violation.code for violation in run_rules(version, DATASET_VERSION_RULES).violations]


@pytest.mark.parametrize(
    "dataset",
    [RAW_BARS_DATASET, ADJUSTED_BARS_DATASET, CORPORATE_ACTIONS_DATASET, UNIVERSE_TIMELINE_DATASET],
    ids=lambda dataset: dataset.dataset_key,
)
def test_a_known_dataset_at_its_own_schema_version_is_accepted(dataset: Any) -> None:
    version = _version(dataset_name=dataset.dataset_key, schema_version=dataset.schema_version)

    assert _codes(version) == []


def test_the_universe_timeline_is_a_known_dataset() -> None:
    assert SUPPORTED_SCHEMA_VERSIONS[UNIVERSE_TIMELINE_DATASET.dataset_key] == {
        UNIVERSE_TIMELINE_DATASET.schema_version
    }


def test_a_dataset_name_no_rule_knows_is_still_rejected() -> None:
    version = _version(dataset_name="something_else")

    assert _codes(version) == ["SCHEMA_VERSION_SUPPORTED_FOR_DATASET"]


def test_a_universe_timeline_at_another_schema_version_is_rejected() -> None:
    version = _version(dataset_name=UNIVERSE_TIMELINE_DATASET.dataset_key, schema_version="9.9.9")

    assert _codes(version) == ["SCHEMA_VERSION_SUPPORTED_FOR_DATASET"]
