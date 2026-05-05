from __future__ import annotations

from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)

from .core import Rule, is_non_negative

SUPPORTED_FEATURE_SCHEMA_VERSIONS: set[str] = {"1.0"}
FINALIZED_STATUSES = {"validated", "complete", "finalized"}


FEATURE_DATASET_VERSION_RULES: list[Rule[FeatureDatasetVersion]] = [
    Rule(
        code="FEATURE_DATASET_VERSION_ID_NON_EMPTY",
        field="dataset_version_id",
        check=lambda fdv, _ctx: (
            isinstance(fdv.dataset_version_id, str) and fdv.dataset_version_id.strip() != ""
        ),
        message=lambda fdv, _ctx: "dataset_version_id must be a non-empty string",
    ),
    Rule(
        code="FEATURE_NAME_NON_EMPTY",
        field="feature_name",
        check=lambda fdv, _ctx: (
            isinstance(fdv.feature_name, str) and fdv.feature_name.strip() != ""
        ),
        message=lambda fdv, _ctx: "feature_name must be a non-empty string",
    ),
    Rule(
        code="DATASET_NAME_IS_FEATURES",
        field="dataset_name",
        check=lambda fdv, _ctx: (
            isinstance(fdv.dataset_name, str) and fdv.dataset_name.strip() == "features"
        ),
        message=lambda fdv, _ctx: "dataset_name must be 'features'",
    ),
    Rule(
        code="SCHEMA_VERSION_NON_EMPTY",
        field="schema_version",
        check=lambda fdv, _ctx: (
            isinstance(fdv.schema_version, str) and fdv.schema_version.strip() != ""
        ),
        message=lambda fdv, _ctx: "schema_version must be a non-empty string",
    ),
    Rule(
        code="SCHEMA_VERSION_SUPPORTED",
        field="schema_version",
        check=lambda fdv, _ctx: fdv.schema_version in SUPPORTED_FEATURE_SCHEMA_VERSIONS,
        message=lambda fdv, _ctx: (
            f"schema_version '{fdv.schema_version}' is not supported for feature datasets"
        ),
    ),
    Rule(
        code="UNDERLYING_DATASET_VERSION_REQUIRED",
        field="source_dataset_version",
        check=lambda fdv, _ctx: (
            isinstance(fdv.source_dataset_version, str) and fdv.source_dataset_version.strip() != ""
        ),
        message=lambda fdv, _ctx: "source_dataset_version must be a non-empty string",
    ),
    Rule(
        code="UNDERLYING_PRICE_BASIS_REQUIRED",
        field="underlying_price_basis",
        check=lambda fdv, _ctx: fdv.underlying_price_basis is not None,
        message=lambda fdv, _ctx: "underlying_price_basis is required",
    ),
    Rule(
        code="COMPUTATION_PARAMETERS_REQUIRED",
        field="computation_parameters",
        check=lambda fdv, _ctx: fdv.computation_parameters is not None,
        message=lambda fdv, _ctx: "computation_parameters is required",
    ),
    Rule(
        code="COMPUTATION_PARAMETERS_NOT_EMPTY",
        field="computation_parameters",
        check=lambda fdv, _ctx: (
            fdv.computation_parameters is None
            or not isinstance(fdv.computation_parameters, dict)
            or len(fdv.computation_parameters) > 0
        ),
        message=lambda fdv, _ctx: (
            "computation_parameters must not be empty when provided as a dict"
        ),
    ),
    Rule(
        code="CREATED_AT_REQUIRED",
        field="created_at",
        check=lambda fdv, _ctx: fdv.created_at is not None,
        message=lambda fdv, _ctx: "created_at is required",
    ),
    Rule(
        code="STORAGE_PATH_NON_EMPTY",
        field="storage_path",
        check=lambda fdv, _ctx: (
            isinstance(fdv.storage_path, str) and fdv.storage_path.strip() != ""
        ),
        message=lambda fdv, _ctx: "storage_path must be a non-empty string",
    ),
    Rule(
        code="SYMBOL_COVERAGE_NON_NEGATIVE_WHEN_PRESENT",
        field="symbol_coverage",
        check=lambda fdv, _ctx: fdv.symbol_coverage is None or is_non_negative(fdv.symbol_coverage),
        message=lambda fdv, _ctx: "symbol_coverage must be >= 0 when present",
    ),
    Rule(
        code="DATE_COVERAGE_BOUNDS_BOTH_PRESENT_OR_BOTH_ABSENT",
        field=None,
        check=lambda fdv, _ctx: (
            (fdv.date_coverage_start is None and fdv.date_coverage_end is None)
            or (fdv.date_coverage_start is not None and fdv.date_coverage_end is not None)
        ),
        message=lambda fdv, _ctx: (
            "date_coverage_start and date_coverage_end must either both be present or both be absent"
        ),
    ),
    Rule(
        code="DATE_COVERAGE_START_LE_END",
        field="date_coverage_start",
        check=lambda fdv, _ctx: (
            fdv.date_coverage_start is None
            or fdv.date_coverage_end is None
            or fdv.date_coverage_start <= fdv.date_coverage_end
        ),
        message=lambda fdv, _ctx: "date_coverage_start must be <= date_coverage_end",
    ),
    Rule(
        code="VALIDATION_STATUS_NON_EMPTY",
        field="validation_status",
        check=lambda fdv, _ctx: (
            isinstance(fdv.validation_status, str) and fdv.validation_status.strip() != ""
        ),
        message=lambda fdv, _ctx: "validation_status must be a non-empty string",
    ),
    Rule(
        code="CHECKSUM_REQUIRED_WHEN_FINALIZED",
        field="checksum",
        check=lambda fdv, _ctx: (
            fdv.validation_status.lower() not in FINALIZED_STATUSES
            or (isinstance(fdv.checksum, str) and fdv.checksum.strip() != "")
        ),
        message=lambda fdv, _ctx: (
            "checksum is required when validation_status is validated/complete/finalized"
        ),
    ),
    Rule(
        code="SOURCE_MANIFEST_REQUIRED_WHEN_FINALIZED",
        field="source_manifest",
        check=lambda fdv, _ctx: (
            fdv.validation_status.lower() not in FINALIZED_STATUSES
            or fdv.source_manifest is not None
        ),
        message=lambda fdv, _ctx: (
            "source_manifest is required when validation_status is validated/complete/finalized"
        ),
    ),
]
