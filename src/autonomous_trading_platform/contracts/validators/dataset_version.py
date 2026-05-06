from __future__ import annotations

from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
    CORPORATE_ACTIONS_DATASET,
    RAW_BARS_DATASET,
)

from .core import Rule, is_non_negative

SUPPORTED_SCHEMA_VERSIONS = {
    RAW_BARS_DATASET.dataset_key: {
        RAW_BARS_DATASET.schema_version,
    },
    ADJUSTED_BARS_DATASET.dataset_key: {
        ADJUSTED_BARS_DATASET.schema_version,
    },
    CORPORATE_ACTIONS_DATASET.dataset_key: {
        CORPORATE_ACTIONS_DATASET.schema_version,
    },
}

DATASET_VERSION_RULES: list[Rule[DatasetVersion]] = [
    Rule(
        code="DATASET_VERSION_ID_NON_EMPTY",
        field="dataset_version_id",
        check=lambda dv, _ctx: (
            isinstance(dv.dataset_version_id, str) and dv.dataset_version_id.strip() != ""
        ),
        message=lambda dv, _ctx: "dataset_version_id must be a non-empty string",
    ),
    Rule(
        code="DATASET_NAME_NON_EMPTY",
        field="dataset_name",
        check=lambda dv, _ctx: isinstance(dv.dataset_name, str) and dv.dataset_name.strip() != "",
        message=lambda dv, _ctx: "dataset_name must be a non-empty string",
    ),
    Rule(
        code="SOURCE_NON_EMPTY",
        field="source",
        check=lambda dv, _ctx: isinstance(dv.source, str) and dv.source.strip() != "",
        message=lambda dv, _ctx: "source must be a non-empty string",
    ),
    Rule(
        code="SCHEMA_VERSION_NON_EMPTY",
        field="schema_version",
        check=lambda dv, _ctx: (
            isinstance(dv.schema_version, str) and dv.schema_version.strip() != ""
        ),
        message=lambda dv, _ctx: "schema_version must be a non-empty string",
    ),
    Rule(
        code="SCHEMA_VERSION_SUPPORTED_FOR_DATASET",
        field="schema_version",
        check=lambda dv, _ctx: (
            dv.dataset_name in SUPPORTED_SCHEMA_VERSIONS
            and dv.schema_version in SUPPORTED_SCHEMA_VERSIONS[dv.dataset_name]
        ),
        message=lambda dv, _ctx: (
            f"schema_version '{dv.schema_version}' is not supported for "
            f"dataset_name '{dv.dataset_name}'"
        ),
    ),
    Rule(
        code="SYMBOL_COVERAGE_NON_NEGATIVE_WHEN_PRESENT",
        field="symbol_coverage",
        check=lambda dv, _ctx: dv.symbol_coverage is None or is_non_negative(dv.symbol_coverage),
        message=lambda dv, _ctx: "symbol_coverage must be >= 0 when present",
    ),
    Rule(
        code="DATE_COVERAGE_BOUNDS_BOTH_PRESENT_OR_BOTH_ABSENT",
        field=None,
        check=lambda dv, _ctx: (
            (dv.date_coverage_start is None and dv.date_coverage_end is None)
            or (dv.date_coverage_start is not None and dv.date_coverage_end is not None)
        ),
        message=lambda dv, _ctx: (
            "date_coverage_start and date_coverage_end must either both be present or both be absent"
        ),
    ),
    Rule(
        code="DATE_COVERAGE_START_LE_END",
        field="date_coverage_start",
        check=lambda dv, _ctx: (
            dv.date_coverage_start is None
            or dv.date_coverage_end is None
            or dv.date_coverage_start <= dv.date_coverage_end
        ),
        message=lambda dv, _ctx: "date_coverage_start must be <= date_coverage_end",
    ),
    Rule(
        code="CHECKSUM_REQUIRED_WHEN_VALIDATED",
        field="checksum",
        check=lambda dv, _ctx: (
            dv.validation_status.lower() not in {"validated", "complete", "finalized"}
            or (dv.checksum is not None and dv.checksum.strip() != "")
        ),
        message=lambda dv, _ctx: (
            "checksum is required when validation_status is validated/complete/finalized"
        ),
    ),
    Rule(
        code="SOURCE_MANIFEST_REQUIRED_WHEN_VALIDATED",
        field="source_manifest",
        check=lambda dv, _ctx: (
            dv.validation_status.lower() not in {"validated", "complete", "finalized"}
            or dv.source_manifest is not None
        ),
        message=lambda dv, _ctx: (
            "source_manifest is required when validation_status is validated/complete/finalized"
        ),
    ),
]
