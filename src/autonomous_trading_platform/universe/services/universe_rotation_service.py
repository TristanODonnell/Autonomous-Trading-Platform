from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    record_universe_rotation_metrics,
    record_universe_validation_metrics,
)
from autonomous_trading_platform.observability.runtime_context import get_runtime_context
from autonomous_trading_platform.storage.sor.models.universe_rotation_records import (
    UniverseRotationRecord,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_rebalance_repository import (
    UniverseRebalanceRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
    UniverseRotationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.universe_rebalance_service import (
    RebalanceResult,
    UniverseRebalanceService,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9._-]*$")
_MIN_UNIVERSE_SIZE = 1
_MAX_UNIVERSE_SIZE = 10_000
logger = get_logger(__name__)


class RotationSafetyError(RuntimeError):
    """Raised when pre-activation safety validation fails."""


@dataclass
class RotationResult:
    previous_version: UniverseVersion | None
    new_version: UniverseVersion
    rebalance_result: RebalanceResult | None
    rotation_record: UniverseRotationRecord
    rotation_type: str
    skipped: bool = False
    skip_reason: str | None = None


class RotationVersionRepository(Protocol):
    def get_candidate_versions(self, limit: int = 20) -> list[UniverseVersion]: ...

    def get_active_version(self, as_of: datetime) -> UniverseVersion | None: ...

    def get_by_version_id(self, version_id: str) -> UniverseVersion | None: ...

    def get_included_members(self, version_id: str) -> list[UniverseMember]: ...

    def insert_version(self, version: UniverseVersion) -> None: ...

    def insert_members(self, members: list[UniverseMember]) -> None: ...

    def retire_active_version(self, retired_at: datetime) -> UniverseVersion | None: ...

    def activate_version(self, version_id: str) -> UniverseVersion: ...


class RotationRebalanceRepository(Protocol):
    def insert_run(self, row: Any) -> None: ...


class RotationRecordRepository(Protocol):
    def insert_record(self, row: UniverseRotationRecord) -> None: ...


class RotationRebalanceService(Protocol):
    def propose_rebalance(
        self,
        *,
        active_version_id: str | None,
        candidate_version_id: str,
        config: UniverseRebalanceConfig,
        name: str | None = None,
    ) -> RebalanceResult: ...


class UniverseRotationService:
    """
    Atomically rotates the active universe version.

    Responsibilities:
    - Load the current active and latest candidate versions
    - Propose a rebalanced universe via UniverseRebalanceService
    - Run pre-activation safety validation
    - Retire the previous active and activate the new version atomically
    - Persist a rotation audit record
    - Support rollback to a prior retired version
    """

    _version_repo: RotationVersionRepository
    _rebalance_repo: RotationRebalanceRepository
    _rotation_repo: RotationRecordRepository
    _rebalance_svc: RotationRebalanceService

    def __init__(self, session: Session) -> None:
        self._session = session
        self._version_repo = UniverseVersionRepository(session)
        self._rebalance_repo = UniverseRebalanceRepository(session)
        self._rotation_repo = UniverseRotationRepository(session)
        self._rebalance_svc = UniverseRebalanceService(session)

    # ------------------------------------------------------------------
    # Scheduled rotation
    # ------------------------------------------------------------------

    def rotate(
        self,
        *,
        candidate_version_id: str | None = None,
        config: UniverseRebalanceConfig | None = None,
        rotation_reason: str = "scheduled",
        force_rotation: bool = False,
        approved_by: str | None = None,
        as_of: datetime | None = None,
    ) -> RotationResult:
        """
        Propose and activate a new universe version from the latest candidate.

        Steps:
        1. Resolve active and candidate versions
        2. Propose rebalance
        3. Safety validation
        4. Atomic: retire previous → activate new → persist rotation record
        """
        started = time.perf_counter()
        now_utc = _ensure_utc(as_of or datetime.now(UTC))
        config = config or UniverseRebalanceConfig()
        ctx = get_runtime_context()

        candidate_version_id = self._resolve_candidate_version_id(candidate_version_id)

        active = self._version_repo.get_active_version(now_utc)
        active_version_id = active.universe_version_id if active else None
        base_log = LogContext(
            component="universe_rotation",
            job="universe_rotation",
            candidate_universe_version_id=candidate_version_id,
            active_universe_version_id=active_version_id,
            correlation_id=ctx.correlation_id if ctx else None,
            job_run_id=ctx.job_run_id if ctx else None,
        ).to_extra()

        rebalance_result = self._rebalance_svc.propose_rebalance(
            active_version_id=active_version_id,
            candidate_version_id=candidate_version_id,
            config=config,
            name=None,
        )

        proposed = rebalance_result.proposed_version
        proposed_members = [
            m for m in rebalance_result.proposed_members if m.excluded_reason is None
        ]

        _validate_pre_activation(
            proposed_version=proposed,
            proposed_members=proposed_members,
            active_version=active,
            force_rotation=force_rotation,
            churn_pct=rebalance_result.churn_pct,
            max_churn_pct=config.max_churn_pct,
        )

        logger.info(
            "universe rotation pre_insert",
            extra={
                **base_log,
                "force_rotation": force_rotation,
                "proposed_version_id": proposed.universe_version_id,
                "proposed_member_count": len(proposed_members),
                "proposed_config_hash": proposed.config_hash,
                "active_config_hash": active.config_hash if active else None,
                "hash_match": proposed.config_hash == (active.config_hash if active else None),
                "churn_pct": rebalance_result.churn_pct,
            },
        )

        if not force_rotation and active is not None and proposed.config_hash == active.config_hash:
            rotation_record = self._build_rotation_record(
                rotation_type="scheduled",
                previous_version=active,
                new_version=proposed,
                rebalance_result=rebalance_result,
                rotation_reason=rotation_reason,
                force_rotation=force_rotation,
                approved_by=approved_by,
                now_utc=now_utc,
                status="skipped",
                rejection_reason="config_hash_unchanged",
            )
            logger.info(
                "universe rotation decision",
                extra={
                    **base_log,
                    "step": "rotation_decision",
                    "rotation_record_id": rotation_record.rotation_id,
                    "rebalance_run_id": rotation_record.rebalance_run_id,
                    "status": "skipped",
                    "reason": "config_hash_unchanged",
                },
            )
            return RotationResult(
                previous_version=active,
                new_version=proposed,
                rebalance_result=rebalance_result,
                rotation_record=rotation_record,
                rotation_type="scheduled",
                skipped=True,
                skip_reason="config_hash_unchanged — universe composition did not change",
            )

        self._version_repo.insert_version(proposed)
        logger.info("universe rotation step", extra={**base_log, "step": "version_inserted"})
        if rebalance_result.proposed_members:
            self._version_repo.insert_members(rebalance_result.proposed_members)
            logger.info(
                "universe rotation step",
                extra={
                    **base_log,
                    "step": "members_inserted",
                    "count": len(rebalance_result.proposed_members),
                },
            )
        self._rebalance_repo.insert_run(rebalance_result.rebalance_run)
        logger.info("universe rotation step", extra={**base_log, "step": "rebalance_run_inserted"})

        # Flush pending inserts so activate_version's get_members() query sees them.
        # autoflush=False on the session means they won't be visible to DB queries otherwise.
        self._session.flush()

        retired = self._version_repo.retire_active_version(proposed.effective_from)
        logger.info(
            "universe rotation step",
            extra={
                **base_log,
                "step": "active_retired",
                "retired_id": retired.universe_version_id if retired else None,
            },
        )
        self._version_repo.activate_version(proposed.universe_version_id)
        logger.info("universe rotation step", extra={**base_log, "step": "version_activated"})

        rotation_record = self._build_rotation_record(
            rotation_type="scheduled",
            previous_version=retired,
            new_version=proposed,
            rebalance_result=rebalance_result,
            rotation_reason=rotation_reason,
            force_rotation=force_rotation,
            approved_by=approved_by,
            now_utc=now_utc,
            status="completed",
            rejection_reason=None,
        )
        self._rotation_repo.insert_record(rotation_record)
        duration = time.perf_counter() - started
        record_universe_rotation_metrics(
            duration_seconds=duration,
            churn_pct=rebalance_result.churn_pct,
            added_count=len(rebalance_result.added),
            removed_count=len(rebalance_result.removed),
            retained_count=len(rebalance_result.retained),
            rotation_type="scheduled",
        )
        logger.info(
            "universe rotation decision",
            extra={
                **base_log,
                "step": "rotation_decision",
                "universe_version_id": proposed.universe_version_id,
                "rotation_record_id": rotation_record.rotation_id,
                "rebalance_run_id": rotation_record.rebalance_run_id,
                "status": "completed",
                "added_count": len(rebalance_result.added),
                "removed_count": len(rebalance_result.removed),
                "retained_count": len(rebalance_result.retained),
                "duration_seconds": duration,
            },
        )

        return RotationResult(
            previous_version=retired,
            new_version=proposed,
            rebalance_result=rebalance_result,
            rotation_record=rotation_record,
            rotation_type="scheduled",
        )

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(
        self,
        *,
        target_version_id: str,
        rollback_reason: str,
        approved_by: str | None = None,
        as_of: datetime | None = None,
    ) -> RotationResult:
        """
        Revert the active universe to a prior version.

        A new PROPOSED version is created copying the target's members so the
        audit trail remains intact and immutability invariants are preserved.
        The current ACTIVE version is retired and the copy is activated.
        """
        started = time.perf_counter()
        now_utc = _ensure_utc(as_of or datetime.now(UTC))
        ctx = get_runtime_context()

        target = self._version_repo.get_by_version_id(target_version_id)
        if target is None:
            raise ValueError(f"Rollback target version '{target_version_id}' not found.")

        target_members = self._version_repo.get_included_members(target_version_id)
        if not target_members:
            raise RotationSafetyError(
                f"Rollback target '{target_version_id}' has no included members. "
                "Cannot rollback to an empty universe."
            )

        rollback_version, rollback_members = self._build_rollback_version(
            target=target,
            target_members=target_members,
            rollback_reason=rollback_reason,
            now_utc=now_utc,
        )

        _validate_pre_activation(
            proposed_version=rollback_version,
            proposed_members=rollback_members,
            active_version=self._version_repo.get_active_version(now_utc),
            force_rotation=True,
            churn_pct=1.0,
            max_churn_pct=1.0,
        )

        self._version_repo.insert_version(rollback_version)
        self._version_repo.insert_members(rollback_members)

        active = self._version_repo.get_active_version(now_utc)
        retired = self._version_repo.retire_active_version(rollback_version.effective_from)
        self._version_repo.activate_version(rollback_version.universe_version_id)

        rotation_record = UniverseRotationRecord(
            rotation_id=str(uuid.uuid4()),
            rotation_type="rollback",
            previous_universe_version_id=active.universe_version_id if active else None,
            candidate_universe_version_id=None,
            new_universe_version_id=rollback_version.universe_version_id,
            rebalance_run_id=None,
            added_symbols_json=None,
            removed_symbols_json=None,
            retained_symbols_json=[m.symbol for m in rollback_members],
            churn_pct=None,
            force_rotation=True,
            rotation_reason=None,
            rollback_reason=rollback_reason,
            approved_by=approved_by,
            approved_at=None,
            approval_required=False,
            created_at=now_utc,
            status="completed",
            rejection_reason=None,
            summary_json={
                "rollback_target_version_id": target_version_id,
                "rollback_version_id": rollback_version.universe_version_id,
                "member_count": len(rollback_members),
                "previous_version_id": active.universe_version_id if active else None,
            },
        )
        self._rotation_repo.insert_record(rotation_record)
        duration = time.perf_counter() - started
        record_universe_rotation_metrics(
            duration_seconds=duration,
            churn_pct=None,
            added_count=0,
            removed_count=0,
            retained_count=len(rollback_members),
            rotation_type="rollback",
        )
        logger.info(
            "universe rollback decision",
            extra={
                **LogContext(
                    component="universe_rotation",
                    job="universe_rollback",
                    step="rollback_decision",
                    universe_version_id=rollback_version.universe_version_id,
                    active_universe_version_id=active.universe_version_id if active else None,
                    rotation_record_id=rotation_record.rotation_id,
                    correlation_id=ctx.correlation_id if ctx else None,
                    job_run_id=ctx.job_run_id if ctx else None,
                ).to_extra(),
                "status": "completed",
                "duration_seconds": duration,
                "retained_count": len(rollback_members),
            },
        )

        return RotationResult(
            previous_version=retired,
            new_version=rollback_version,
            rebalance_result=None,
            rotation_record=rotation_record,
            rotation_type="rollback",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_candidate_version_id(self, candidate_version_id: str | None) -> str:
        if candidate_version_id is not None:
            return candidate_version_id
        candidates = self._version_repo.get_candidate_versions(limit=1)
        if not candidates:
            raise RuntimeError(
                "No candidate universe versions found. Run 'universe candidate-generate' first."
            )
        return cast(str, candidates[0].universe_version_id)

    def _build_rollback_version(
        self,
        target: UniverseVersion,
        target_members: list[UniverseMember],
        rollback_reason: str,
        now_utc: datetime,
    ) -> tuple[UniverseVersion, list[UniverseMember]]:
        version_id = str(uuid.uuid4())
        symbols = sorted(m.symbol for m in target_members)
        config_hash = _compute_symbols_hash(symbols)

        rollback_version = UniverseVersion(
            universe_version_id=version_id,
            name=f"rollback_{target.universe_version_id[:8]}_{now_utc.date().isoformat()}",
            source=UniverseSource.REBALANCE,
            created_at=now_utc,
            effective_from=now_utc,
            effective_to=None,
            status=UniverseStatus.PROPOSED,
            rebalance_reason=f"rollback:{rollback_reason}",
            config_hash=config_hash,
            generation_metadata_json={
                "schema_version": 1,
                "rebalance_type": "rollback",
                "rollback_target_version_id": target.universe_version_id,
                "rollback_reason": rollback_reason,
                "member_count": len(symbols),
                "generation_timestamp": now_utc.isoformat(),
            },
        )

        rollback_members = [
            UniverseMember(
                universe_version_id=version_id,
                symbol=m.symbol,
                rank=m.rank,
                score=m.score,
                included_reason="rollback",
                excluded_reason=None,
                liquidity_metrics_json=m.liquidity_metrics_json,
                quality_metrics_json=m.quality_metrics_json,
            )
            for m in target_members
        ]

        return rollback_version, rollback_members

    def _build_rotation_record(
        self,
        *,
        rotation_type: str,
        previous_version: UniverseVersion | None,
        new_version: UniverseVersion,
        rebalance_result: RebalanceResult,
        rotation_reason: str,
        force_rotation: bool,
        approved_by: str | None,
        now_utc: datetime,
        status: str,
        rejection_reason: str | None,
    ) -> UniverseRotationRecord:
        run = rebalance_result.rebalance_run
        return UniverseRotationRecord(
            rotation_id=str(uuid.uuid4()),
            rotation_type=rotation_type,
            previous_universe_version_id=(
                previous_version.universe_version_id if previous_version else None
            ),
            candidate_universe_version_id=run.candidate_universe_version_id,
            new_universe_version_id=new_version.universe_version_id,
            rebalance_run_id=run.rebalance_run_id,
            added_symbols_json=rebalance_result.added,
            removed_symbols_json=rebalance_result.removed,
            retained_symbols_json=rebalance_result.retained,
            churn_pct=rebalance_result.churn_pct,
            force_rotation=force_rotation,
            rotation_reason=rotation_reason,
            rollback_reason=None,
            approved_by=approved_by,
            approved_at=None,
            approval_required=False,
            created_at=now_utc,
            status=status,
            rejection_reason=rejection_reason,
            summary_json=rebalance_result.audit_summary,
        )


# ------------------------------------------------------------------
# Module-level safety validation (pure, no I/O)
# ------------------------------------------------------------------


def _validate_pre_activation(
    *,
    proposed_version: UniverseVersion,
    proposed_members: list[UniverseMember],
    active_version: UniverseVersion | None,
    force_rotation: bool,
    churn_pct: float,
    max_churn_pct: float,
) -> None:
    errors: list[str] = []

    if not proposed_members:
        errors.append("Proposed universe has no included members.")

    symbols = [m.symbol for m in proposed_members]
    if len(symbols) != len(set(symbols)):
        errors.append("Proposed universe contains duplicate symbols.")

    invalid = [s for s in symbols if _SYMBOL_RE.fullmatch(s) is None]
    if invalid:
        errors.append(f"Proposed universe contains invalid symbol format: {invalid[:5]}")

    if not (_MIN_UNIVERSE_SIZE <= len(symbols) <= _MAX_UNIVERSE_SIZE):
        errors.append(
            f"Proposed universe size {len(symbols)} is outside expected bounds "
            f"[{_MIN_UNIVERSE_SIZE}, {_MAX_UNIVERSE_SIZE}]."
        )

    if not force_rotation and active_version is not None and churn_pct > max_churn_pct:
        errors.append(
            f"Churn {churn_pct:.2%} exceeds max_churn_pct {max_churn_pct:.2%}. "
            "Pass force_rotation=True to override."
        )

    if errors:
        record_universe_validation_metrics(
            invalid_member_count=len(errors),
            failure_count=len(errors),
        )
        logger.error(
            "universe validation failure",
            extra={
                **LogContext(
                    component="universe_rotation",
                    step="universe_validation_failure",
                    universe_version_id=proposed_version.universe_version_id,
                ).to_extra(),
                "errors": errors,
            },
        )
        raise RotationSafetyError(
            "Universe rotation failed pre-activation safety checks:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def _compute_symbols_hash(symbols: list[str]) -> str:
    payload = json.dumps({"symbols": sorted(symbols)}, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
