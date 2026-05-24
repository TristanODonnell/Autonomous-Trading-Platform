"""Versioned store for SlippageCalibrationSnapshot instances.

Provides in-memory storage with optional JSON file persistence.  Matching the
pattern used by SimulationResultCache, persistence is opt-in: omit storage_path
to run entirely in-memory.

Snapshots are keyed by calibration_id (UUID string) and ordered by generated_at.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.calibration.models.calibration_snapshot import (
    SlippageCalibrationSnapshot,
)

logger = get_logger(__name__)


class CalibrationSnapshotStore:
    """Thread-safe store for SlippageCalibrationSnapshot instances.

    Usage:
        store = CalibrationSnapshotStore()                        # in-memory only
        store = CalibrationSnapshotStore(storage_path="cal.json") # with persistence

    Persistence format: JSON list of snapshot dicts, newest last.
    """

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, SlippageCalibrationSnapshot] = {}
        self._storage_path = Path(storage_path) if storage_path is not None else None
        if self._storage_path is not None:
            self._load_from_disk()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, snapshot: SlippageCalibrationSnapshot) -> None:
        """Persist a calibration snapshot.  Overwrites any existing entry with the same id."""
        with self._lock:
            self._snapshots[snapshot.calibration_id] = snapshot
            if self._storage_path is not None:
                self._flush_to_disk()
        logger.debug(
            "calibration.store.saved",
            extra={"calibration_id": snapshot.calibration_id},
        )

    def load(self, calibration_id: str) -> SlippageCalibrationSnapshot | None:
        """Return snapshot by id, or None if not found."""
        with self._lock:
            return self._snapshots.get(calibration_id)

    def load_latest(self) -> SlippageCalibrationSnapshot | None:
        """Return the most recently generated snapshot, or None if the store is empty."""
        with self._lock:
            if not self._snapshots:
                return None
            return max(self._snapshots.values(), key=lambda s: s.generated_at)

    def list_ids(self) -> list[str]:
        """Return all calibration_ids, ordered by generated_at ascending."""
        with self._lock:
            return [
                s.calibration_id
                for s in sorted(self._snapshots.values(), key=lambda s: s.generated_at)
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._snapshots)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _flush_to_disk(self) -> None:
        assert self._storage_path is not None
        ordered = sorted(self._snapshots.values(), key=lambda s: s.generated_at)
        payload = json.dumps(
            [s.to_dict() for s in ordered],
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._storage_path.write_text(payload, encoding="utf-8")
        except OSError:
            logger.exception(
                "calibration.store.flush_failed",
                extra={"path": str(self._storage_path)},
            )

    def _load_from_disk(self) -> None:
        assert self._storage_path is not None
        if not self._storage_path.exists():
            return
        try:
            raw = self._storage_path.read_text(encoding="utf-8")
            items = json.loads(raw)
            for item in items:
                snap = SlippageCalibrationSnapshot.from_dict(item)
                self._snapshots[snap.calibration_id] = snap
            logger.debug(
                "calibration.store.loaded",
                extra={
                    "path": str(self._storage_path),
                    "count": len(self._snapshots),
                },
            )
        except Exception:
            logger.exception(
                "calibration.store.load_failed",
                extra={"path": str(self._storage_path)},
            )
