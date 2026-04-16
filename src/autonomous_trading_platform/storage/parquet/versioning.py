from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def generate_dataset_version(dataset_name: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid4().hex[:8]
    normalized_name = dataset_name.lower().replace("/", "_").replace(" ", "_")
    return f"{normalized_name}_{timestamp}_{suffix}"
