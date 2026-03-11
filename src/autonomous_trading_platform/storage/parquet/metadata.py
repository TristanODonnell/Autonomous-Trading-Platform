from __future__ import annotations

from collections.abc import Mapping

import pyarrow as pa

from .datasets import ParquetDataset

REQUIRED_METADATA_KEYS = (
    "dataset_name",
    "schema_version",
    "data_version",
    "ingestion_timestamp",
    "checksum",
)


def build_metadata(
    dataset: ParquetDataset,
    data_version: str,
    ingestion_timestamp: str,
    checksum: str,
) -> dict[str, str]:
    return {
        "dataset_name": dataset.name,
        "schema_version": dataset.schema_version,
        "data_version": data_version,
        "ingestion_timestamp": ingestion_timestamp,
        "checksum": checksum,
    }


def encode_metadata(metadata: Mapping[str, str]) -> dict[bytes, bytes]:
    return {key.encode("utf-8"): value.encode("utf-8") for key, value in metadata.items()}


def decode_metadata(metadata: Mapping[bytes, bytes] | None) -> dict[str, str]:
    if metadata is None:
        return {}

    return {key.decode("utf-8"): value.decode("utf-8") for key, value in metadata.items()}


def attach_metadata(
    schema: pa.Schema,
    metadata: Mapping[str, str],
) -> pa.Schema:
    encoded_new = encode_metadata(metadata)
    existing = schema.metadata or {}
    merged = {**existing, **encoded_new}
    return schema.with_metadata(merged)


def extract_metadata(schema: pa.Schema) -> dict[str, str]:
    return decode_metadata(schema.metadata)


def validate_required_metadata(metadata: Mapping[str, str]) -> None:
    missing = [key for key in REQUIRED_METADATA_KEYS if key not in metadata]
    if missing:
        raise ValueError(f"Missing required parquet metadata keys: {missing}")
