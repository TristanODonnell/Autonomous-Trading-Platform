"""Publish versioned Parquet datasets from Box A to an object store for the worker.

Dataset versions are immutable directories ``<DATA_ROOT>/.../dataset_version=<id>/...``.
Each is published once: its files go to ``<prefix>/<same relative path>``, then a manifest
``<prefix>/manifests/<id>.json`` is written last as the commit marker. A version whose
manifest exists is never re-uploaded, so the step is idempotent and a run that dies
mid-upload is finished by the next one. Nothing is ever deleted from the store.

The worker syncs ``<prefix>/`` into its own DATA_ROOT and gets the identical layout; the
manifests tell it exactly which versions exist and what each one covers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.parquet.object_store import ObjectStore
from autonomous_trading_platform.storage.parquet.paths import get_data_root
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

logger = get_logger(__name__)

MANIFEST_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class PublishedVersion:
    dataset_version_id: str
    manifest_key: str
    file_count: int
    bytes_uploaded: int


@dataclass
class PublishResult:
    published: list[PublishedVersion] = field(default_factory=list)
    already_published: list[str] = field(default_factory=list)
    index_key: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "published": [p.dataset_version_id for p in self.published],
            "already_published_count": len(self.already_published),
            "files_uploaded": sum(p.file_count for p in self.published),
            "bytes_uploaded": sum(p.bytes_uploaded for p in self.published),
            "index_key": self.index_key,
        }


class DatasetPublishService:
    def __init__(
        self,
        store: ObjectStore,
        *,
        session: Session | None = None,
        data_root: Path | None = None,
        prefix: str = "datasets",
        git_sha: str | None = None,
    ) -> None:
        self._store = store
        self._session = session
        self._data_root = data_root or get_data_root()
        self._prefix = prefix.strip("/")
        self._git_sha = git_sha

    # ------------------------------------------------------------------ public

    def local_versions(self) -> dict[str, Path]:
        """Every ``dataset_version=<id>`` directory under the data root, by id."""
        found: dict[str, Path] = {}
        if not self._data_root.exists():
            return found
        for path in sorted(self._data_root.glob("**/dataset_version=*")):
            if path.is_dir():
                found[path.name.split("=", 1)[1]] = path
        return found

    def manifest_key(self, dataset_version_id: str) -> str:
        return f"{self._prefix}/manifests/{dataset_version_id}.json"

    def publish_new_versions(
        self, *, trading_date: date, now_utc: datetime | None = None
    ) -> PublishResult:
        """Publish every local version without a manifest, then write the day's index."""
        now_utc = now_utc or datetime.now(UTC)
        result = PublishResult()
        for version_id, path in self.local_versions().items():
            if self._store.exists(self.manifest_key(version_id)):
                result.already_published.append(version_id)
                continue
            result.published.append(self._publish_version(version_id, path, now_utc))

        index_key = f"{self._prefix}/published/{trading_date.isoformat()}.json"
        self._store.put_bytes(
            _json_bytes(
                {
                    "schema_version": MANIFEST_SCHEMA_VERSION,
                    "trading_date": trading_date.isoformat(),
                    "published_at": now_utc.isoformat(),
                    "git_sha": self._git_sha,
                    "published": [p.dataset_version_id for p in result.published],
                    "already_published": result.already_published,
                }
            ),
            index_key,
        )
        result.index_key = index_key
        logger.info("dataset_publish.finished", extra=result.summary())
        return result

    # ----------------------------------------------------------------- private

    def _publish_version(self, version_id: str, path: Path, now_utc: datetime) -> PublishedVersion:
        files: list[dict[str, Any]] = []
        total = 0
        for local in sorted(p for p in path.rglob("*") if p.is_file()):
            relative = local.relative_to(self._data_root).as_posix()
            key = f"{self._prefix}/{relative}"
            size = local.stat().st_size
            self._store.put_file(local, key)
            files.append({"key": key, "size": size, "sha256": _sha256(local)})
            total += size

        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "dataset_version_id": version_id,
            "root": f"{self._prefix}/{path.relative_to(self._data_root).as_posix()}",
            "published_at": now_utc.isoformat(),
            "git_sha": self._git_sha,
            "files": files,
            "dataset_version": self._registry_row(version_id),
        }
        key = self.manifest_key(version_id)
        self._store.put_bytes(_json_bytes(manifest), key)
        logger.info(
            "dataset_publish.version_published",
            extra={"dataset_version_id": version_id, "files": len(files), "bytes": total},
        )
        return PublishedVersion(version_id, key, len(files), total)

    def _registry_row(self, version_id: str) -> dict[str, Any] | None:
        """The ``dataset_versions`` row, when the id is registered there."""
        if self._session is None:
            return None
        row = self._session.get(DatasetVersions, version_id)
        if row is None:
            return None
        return {
            "dataset_name": row.dataset_name,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "source": row.source,
            "price_basis": getattr(row.price_basis, "value", row.price_basis),
            "interval": getattr(row.interval, "value", row.interval),
            "schema_version": row.schema_version,
            "symbol_coverage": row.symbol_coverage,
            "date_coverage_start": _iso(row.date_coverage_start),
            "date_coverage_end": _iso(row.date_coverage_end),
            "validation_status": row.validation_status,
            "checksum": row.checksum,
            "source_dataset_version": row.source_dataset_version,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
