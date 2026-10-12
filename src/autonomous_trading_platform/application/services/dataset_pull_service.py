"""Pull published Parquet dataset versions from the object store into the worker's DATA_ROOT.

Mirror image of ``DatasetPublishService``. Box A publishes each immutable
``dataset_version=<id>`` directory plus a manifest ``<prefix>/manifests/<id>.json``; the
worker reads those manifests, downloads every file it does not already hold (verified by
size and SHA-256), and registers the version in its *own* ``dataset_versions`` table from
the registry row carried in the manifest. The local layout is identical to Box A's, so the
same resolvers and readers work unchanged.

Idempotent: files already present with the right size are skipped, a partial download is
written to ``<file>.partial`` and renamed only after the hash matches, and a registered
version is left as it is. Nothing is ever deleted locally or remotely.

Pinning: ``as_of`` selects the day index ``<prefix>/published/<date>.json`` that Box A wrote
at the end of that session; the set of versions it lists (new and previously published) is
exactly what A held at that point.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.parquet.object_store import ObjectStore
from autonomous_trading_platform.storage.parquet.paths import get_data_root
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

logger = get_logger(__name__)


class DatasetPullError(RuntimeError):
    """A manifest, index or downloaded file did not match what Box A published."""


@dataclass(frozen=True)
class PulledVersion:
    dataset_version_id: str
    files_downloaded: int
    files_present: int
    bytes_downloaded: int
    registered: bool


@dataclass
class PullResult:
    pulled: list[PulledVersion] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    as_of: date | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "pulled": [p.dataset_version_id for p in self.pulled],
            "unchanged_count": len(self.unchanged),
            "files_downloaded": sum(p.files_downloaded for p in self.pulled),
            "bytes_downloaded": sum(p.bytes_downloaded for p in self.pulled),
            "registered": [p.dataset_version_id for p in self.pulled if p.registered],
        }


class DatasetPullService:
    def __init__(
        self,
        store: ObjectStore,
        *,
        session: Session | None = None,
        data_root: Path | None = None,
        prefix: str = "datasets",
    ) -> None:
        self._store = store
        self._session = session
        self._data_root = data_root or get_data_root()
        self._prefix = prefix.strip("/")

    # ------------------------------------------------------------------ public

    def manifest_key(self, dataset_version_id: str) -> str:
        return f"{self._prefix}/manifests/{dataset_version_id}.json"

    def remote_versions(self) -> list[str]:
        """Every dataset version with a manifest in the store (i.e. fully published)."""
        prefix = f"{self._prefix}/manifests/"
        return sorted(
            key[len(prefix) : -len(".json")]
            for key in self._store.list_keys(prefix)
            if key.endswith(".json")
        )

    def versions_as_of(self, trading_date: date) -> list[str]:
        """The versions Box A held after the given session, from that day's index."""
        key = f"{self._prefix}/published/{trading_date.isoformat()}.json"
        if not self._store.exists(key):
            raise DatasetPullError(f"no publish index for {trading_date.isoformat()} ({key})")
        index = json.loads(self._store.get_bytes(key))
        return sorted({*index.get("published", []), *index.get("already_published", [])})

    def pull(
        self,
        *,
        versions: list[str] | None = None,
        as_of: date | None = None,
        register: bool = True,
    ) -> PullResult:
        """Download and register the selected versions (default: every published one)."""
        if versions is not None and as_of is not None:
            raise ValueError("pass either versions or as_of, not both")
        if as_of is not None:
            selected = self.versions_as_of(as_of)
        elif versions is not None:
            selected = sorted(set(versions))
        else:
            selected = self.remote_versions()

        result = PullResult(as_of=as_of)
        for version_id in selected:
            pulled = self._pull_version(version_id, register=register)
            if pulled.files_downloaded == 0 and not pulled.registered:
                result.unchanged.append(version_id)
            else:
                result.pulled.append(pulled)
        logger.info("dataset_pull.finished", extra=result.summary())
        return result

    # ----------------------------------------------------------------- private

    def _pull_version(self, version_id: str, *, register: bool) -> PulledVersion:
        manifest_key = self.manifest_key(version_id)
        if not self._store.exists(manifest_key):
            raise DatasetPullError(f"dataset version {version_id} has no manifest ({manifest_key})")
        manifest = json.loads(self._store.get_bytes(manifest_key))
        if manifest.get("dataset_version_id") != version_id:
            raise DatasetPullError(
                f"manifest {manifest_key} is for {manifest.get('dataset_version_id')!r}"
            )

        downloaded = present = total = 0
        for entry in manifest.get("files", []):
            local = self._local_path(entry["key"])
            if local.exists() and local.stat().st_size == entry["size"]:
                present += 1
                continue
            self._download(entry, local)
            downloaded += 1
            total += entry["size"]

        registered = False
        if register and self._session is not None:
            registered = self._register(version_id, manifest)

        logger.info(
            "dataset_pull.version_pulled",
            extra={
                "dataset_version_id": version_id,
                "files_downloaded": downloaded,
                "files_present": present,
                "bytes": total,
                "registered": registered,
            },
        )
        return PulledVersion(version_id, downloaded, present, total, registered)

    def _local_path(self, key: str) -> Path:
        prefix = f"{self._prefix}/"
        if not key.startswith(prefix):
            raise DatasetPullError(f"object key {key!r} is outside prefix {self._prefix!r}")
        relative = Path(key[len(prefix) :])
        if relative.is_absolute() or ".." in relative.parts:
            raise DatasetPullError(f"refusing unsafe object key {key!r}")
        return self._data_root / relative

    def _download(self, entry: dict[str, Any], local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        partial = local.with_name(local.name + ".partial")
        try:
            self._store.get_file(entry["key"], partial)
            size = partial.stat().st_size
            if size != entry["size"]:
                raise DatasetPullError(
                    f"{entry['key']}: downloaded {size} bytes, manifest says {entry['size']}"
                )
            if _sha256(partial) != entry["sha256"]:
                raise DatasetPullError(f"{entry['key']}: sha256 mismatch after download")
            partial.replace(local)
        finally:
            partial.unlink(missing_ok=True)

    def _register(self, version_id: str, manifest: dict[str, Any]) -> bool:
        """Insert the ``dataset_versions`` row from the manifest unless already present.

        Returns True when a row was inserted. A manifest without a registry row (the
        version was never in Box A's ``dataset_versions``) is skipped with a warning; its
        Parquet files are still usable by explicit id.
        """
        assert self._session is not None
        if self._session.get(DatasetVersions, version_id) is not None:
            return False
        row = manifest.get("dataset_version")
        if not row:
            logger.warning(
                "dataset_pull.no_registry_row",
                extra={"dataset_version_id": version_id},
            )
            return False
        metadata = dict(row.get("metadata_json") or {})
        metadata["pulled_from"] = {
            "manifest_key": self.manifest_key(version_id),
            "published_at": manifest.get("published_at"),
            "publisher_git_sha": manifest.get("git_sha"),
        }
        self._session.add(
            DatasetVersions(
                dataset_version_id=version_id,
                dataset_name=row["dataset_name"],
                created_at=_parse_datetime(row.get("created_at")),
                source=row["source"],
                price_basis=PriceBasis(row["price_basis"]),
                interval=BarInterval(row["interval"]),
                schema_version=row["schema_version"],
                symbol_coverage=row.get("symbol_coverage"),
                date_coverage_start=_parse_date(row.get("date_coverage_start")),
                date_coverage_end=_parse_date(row.get("date_coverage_end")),
                validation_status=row["validation_status"],
                checksum=row.get("checksum"),
                source_dataset_version=row.get("source_dataset_version"),
                source_manifest=row.get("source_manifest"),
                metadata_json=metadata,
            )
        )
        self._session.flush()
        return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        raise DatasetPullError("manifest registry row has no created_at")
    return datetime.fromisoformat(value)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
