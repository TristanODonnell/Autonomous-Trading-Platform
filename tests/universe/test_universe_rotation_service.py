from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.storage.sor.models.rebalance_runs import UniverseRebalanceRun
from autonomous_trading_platform.storage.sor.models.universe_rotation_records import (
    UniverseRotationRecord,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.universe.jobs.run_universe_rotation import should_rotate
from autonomous_trading_platform.universe.services.universe_rebalance_service import RebalanceResult
from autonomous_trading_platform.universe.services.universe_rotation_service import (
    RotationSafetyError,
    UniverseRotationService,
    _validate_pre_activation,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig

_T0 = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)
_T1 = datetime(2025, 6, 2, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_version(
    *,
    version_id: str | None = None,
    status: str = UniverseStatus.ACTIVE,
    effective_from: datetime = _T0,
    effective_to: datetime | None = None,
    config_hash: str = "hash-abc",
    source: str = UniverseSource.REBALANCE,
    rebalance_reason: str | None = "scheduled",
) -> UniverseVersion:
    return UniverseVersion(
        universe_version_id=version_id or str(uuid.uuid4()),
        name="test_version",
        source=source,
        created_at=_T0,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        rebalance_reason=rebalance_reason,
        config_hash=config_hash,
        generation_metadata_json=None,
    )


def _make_member(
    version_id: str,
    symbol: str,
    rank: int = 1,
    excluded_reason: str | None = None,
) -> UniverseMember:
    return UniverseMember(
        universe_version_id=version_id,
        symbol=symbol,
        rank=rank,
        score=None,
        included_reason="test" if excluded_reason is None else None,
        excluded_reason=excluded_reason,
    )


def _make_members(version_id: str, symbols: list[str]) -> list[UniverseMember]:
    return [_make_member(version_id, sym, i + 1) for i, sym in enumerate(symbols)]


def _make_rebalance_run(
    *,
    run_id: str | None = None,
    previous_version_id: str | None,
    candidate_version_id: str,
    proposed_version_id: str,
    added: list[str],
    removed: list[str],
    retained: list[str],
    churn_pct: float = 0.05,
) -> UniverseRebalanceRun:
    return UniverseRebalanceRun(
        rebalance_run_id=run_id or str(uuid.uuid4()),
        previous_universe_version_id=previous_version_id,
        candidate_universe_version_id=candidate_version_id,
        proposed_universe_version_id=proposed_version_id,
        added_symbols_json=added,
        removed_symbols_json=removed,
        retained_symbols_json=retained,
        churn_pct=churn_pct,
        target_universe_size=500,
        max_churn_pct=0.15,
        config_json={},
        summary_json={},
        created_at=_T0,
        status="accepted",
        rejection_reason=None,
    )


def _make_rebalance_result(
    *,
    proposed_version: UniverseVersion,
    proposed_members: list[UniverseMember],
    previous_version_id: str | None,
    candidate_version_id: str,
    added: list[str],
    removed: list[str],
    retained: list[str],
    churn_pct: float = 0.05,
) -> RebalanceResult:
    run = _make_rebalance_run(
        previous_version_id=previous_version_id,
        candidate_version_id=candidate_version_id,
        proposed_version_id=proposed_version.universe_version_id,
        added=added,
        removed=removed,
        retained=retained,
        churn_pct=churn_pct,
    )
    return RebalanceResult(
        proposed_version=proposed_version,
        proposed_members=proposed_members,
        rebalance_run=run,
        added=added,
        removed=removed,
        retained=retained,
        skipped_adds=[],
        skipped_removes=[],
        churn_pct=churn_pct,
        audit_summary={
            "previous_universe_size": len(retained) + len(removed),
            "candidate_universe_size": len(added) + len(retained),
            "proposed_universe_size": len(added) + len(retained),
            "added_count": len(added),
            "removed_count": len(removed),
            "retained_count": len(retained),
            "churn_pct": churn_pct,
        },
    )


# ---------------------------------------------------------------------------
# Stub implementations
# ---------------------------------------------------------------------------


@dataclass
class StubVersionRepo:
    active_version: UniverseVersion | None = None
    versions: dict[str, UniverseVersion] = field(default_factory=dict)
    members_by_id: dict[str, list[UniverseMember]] = field(default_factory=dict)
    candidates: list[UniverseVersion] = field(default_factory=list)
    inserted_versions: list[UniverseVersion] = field(default_factory=list)
    inserted_members: list[UniverseMember] = field(default_factory=list)
    retired_version: UniverseVersion | None = None
    activated_version_id: str | None = None

    def get_active_version(self, as_of: datetime) -> UniverseVersion | None:
        return self.active_version

    def get_by_version_id(self, version_id: str) -> UniverseVersion | None:
        return self.versions.get(version_id)

    def get_included_members(self, version_id: str) -> list[UniverseMember]:
        return [m for m in self.members_by_id.get(version_id, []) if m.excluded_reason is None]

    def get_members(self, version_id: str) -> list[UniverseMember]:
        return self.members_by_id.get(version_id, [])

    def get_candidate_versions(self, limit: int = 20) -> list[UniverseVersion]:
        return self.candidates[:limit]

    def insert_version(self, row: UniverseVersion) -> None:
        self.inserted_versions.append(row)
        self.versions[row.universe_version_id] = row

    def insert_members(self, rows: list[UniverseMember]) -> None:
        self.inserted_members.extend(rows)
        for m in rows:
            self.members_by_id.setdefault(m.universe_version_id, []).append(m)

    def activate_version(self, version_id: str) -> UniverseVersion:
        v = self.versions.get(version_id)
        assert v is not None, f"Version {version_id} not in stub"
        v.status = UniverseStatus.ACTIVE
        self.active_version = v
        self.activated_version_id = version_id
        return v

    def retire_active_version(self, retired_at: datetime) -> UniverseVersion | None:
        if self.active_version is not None:
            self.active_version.status = UniverseStatus.RETIRED
            self.active_version.effective_to = retired_at
            self.retired_version = self.active_version
            old_active = self.active_version
            self.active_version = None
            return old_active
        return None


@dataclass
class StubRebalanceRepo:
    inserted_runs: list[UniverseRebalanceRun] = field(default_factory=list)

    def insert_run(self, row: UniverseRebalanceRun) -> None:
        self.inserted_runs.append(row)


@dataclass
class StubRotationRepo:
    inserted_records: list[UniverseRotationRecord] = field(default_factory=list)

    def insert_record(self, row: UniverseRotationRecord) -> None:
        self.inserted_records.append(row)


@dataclass
class StubRebalanceService:
    result: RebalanceResult

    def propose_rebalance(
        self,
        *,
        active_version_id: str | None,
        candidate_version_id: str,
        config: UniverseRebalanceConfig,
        name: str | None = None,
    ) -> RebalanceResult:
        return self.result


class _ServiceUnderTest(UniverseRotationService):
    """Rotation service with injectable stub dependencies."""

    def __init__(
        self,
        version_repo: StubVersionRepo,
        rebalance_repo: StubRebalanceRepo,
        rotation_repo: StubRotationRepo,
        rebalance_svc: StubRebalanceService,
    ) -> None:
        self._version_repo = version_repo
        self._rebalance_repo = rebalance_repo
        self._rotation_repo = rotation_repo
        self._rebalance_svc = rebalance_svc


def _make_service(
    *,
    active: UniverseVersion | None = None,
    candidates: list[UniverseVersion] | None = None,
    rebalance_result: RebalanceResult,
    extra_versions: dict[str, UniverseVersion] | None = None,
    extra_members: dict[str, list[UniverseMember]] | None = None,
) -> tuple[_ServiceUnderTest, StubVersionRepo, StubRebalanceRepo, StubRotationRepo]:
    version_repo = StubVersionRepo(
        active_version=active,
        candidates=candidates or [],
        versions={v.universe_version_id: v for v in (extra_versions or {}).values()},
        members_by_id=extra_members or {},
    )
    if active:
        version_repo.versions[active.universe_version_id] = active
    rebalance_repo = StubRebalanceRepo()
    rotation_repo = StubRotationRepo()
    rebalance_svc = StubRebalanceService(result=rebalance_result)
    svc = _ServiceUnderTest(version_repo, rebalance_repo, rotation_repo, rebalance_svc)
    return svc, version_repo, rebalance_repo, rotation_repo


# ---------------------------------------------------------------------------
# Tests: _validate_pre_activation (pure function)
# ---------------------------------------------------------------------------


class TestValidatePreActivation:
    def _proposed(self, symbols: list[str]) -> tuple[UniverseVersion, list[UniverseMember]]:
        v = _make_version(status=UniverseStatus.PROPOSED)
        members = _make_members(v.universe_version_id, symbols)
        return v, members

    def test_passes_for_valid_input(self) -> None:
        v, members = self._proposed(["AAPL", "MSFT", "GOOG"])
        _validate_pre_activation(
            proposed_version=v,
            proposed_members=members,
            active_version=None,
            force_rotation=False,
            churn_pct=0.0,
            max_churn_pct=0.15,
        )

    def test_raises_for_empty_members(self) -> None:
        v, _ = self._proposed([])
        with pytest.raises(RotationSafetyError, match="no included members"):
            _validate_pre_activation(
                proposed_version=v,
                proposed_members=[],
                active_version=None,
                force_rotation=False,
                churn_pct=0.0,
                max_churn_pct=0.15,
            )

    def test_raises_for_duplicate_symbols(self) -> None:
        v = _make_version(status=UniverseStatus.PROPOSED)
        m1 = _make_member(v.universe_version_id, "AAPL")
        m2 = _make_member(v.universe_version_id, "AAPL")
        with pytest.raises(RotationSafetyError, match="duplicate symbols"):
            _validate_pre_activation(
                proposed_version=v,
                proposed_members=[m1, m2],
                active_version=None,
                force_rotation=False,
                churn_pct=0.0,
                max_churn_pct=0.15,
            )

    def test_raises_for_invalid_symbol_format(self) -> None:
        v = _make_version(status=UniverseStatus.PROPOSED)
        m = _make_member(v.universe_version_id, "123invalid")
        with pytest.raises(RotationSafetyError, match="invalid symbol format"):
            _validate_pre_activation(
                proposed_version=v,
                proposed_members=[m],
                active_version=None,
                force_rotation=False,
                churn_pct=0.0,
                max_churn_pct=0.15,
            )

    def test_raises_when_churn_exceeds_max_without_force(self) -> None:
        v, members = self._proposed(["AAPL", "MSFT"])
        active = _make_version(status=UniverseStatus.ACTIVE, config_hash="different")
        with pytest.raises(RotationSafetyError, match="Churn"):
            _validate_pre_activation(
                proposed_version=v,
                proposed_members=members,
                active_version=active,
                force_rotation=False,
                churn_pct=0.50,
                max_churn_pct=0.15,
            )

    def test_passes_when_churn_exceeds_max_with_force(self) -> None:
        v, members = self._proposed(["AAPL", "MSFT"])
        active = _make_version(status=UniverseStatus.ACTIVE, config_hash="different")
        _validate_pre_activation(
            proposed_version=v,
            proposed_members=members,
            active_version=active,
            force_rotation=True,
            churn_pct=0.90,
            max_churn_pct=0.15,
        )

    def test_passes_churn_check_when_no_active(self) -> None:
        v, members = self._proposed(["AAPL"])
        _validate_pre_activation(
            proposed_version=v,
            proposed_members=members,
            active_version=None,
            force_rotation=False,
            churn_pct=1.0,
            max_churn_pct=0.15,
        )

    def test_raises_for_oversized_universe(self) -> None:
        v = _make_version(status=UniverseStatus.PROPOSED)
        symbols = [f"S{i:05d}" for i in range(10_001)]
        members = [_make_member(v.universe_version_id, s) for s in symbols]
        with pytest.raises(RotationSafetyError, match="outside expected bounds"):
            _validate_pre_activation(
                proposed_version=v,
                proposed_members=members,
                active_version=None,
                force_rotation=False,
                churn_pct=0.0,
                max_churn_pct=0.15,
            )


# ---------------------------------------------------------------------------
# Tests: rotate()
# ---------------------------------------------------------------------------


class TestRotate:
    def _build(
        self,
        *,
        active: UniverseVersion | None,
        candidate: UniverseVersion,
        proposed: UniverseVersion,
        proposed_included: list[str],
        added: list[str],
        removed: list[str],
        retained: list[str],
        churn_pct: float = 0.05,
    ) -> tuple[_ServiceUnderTest, StubVersionRepo, StubRebalanceRepo, StubRotationRepo]:
        proposed_members = _make_members(proposed.universe_version_id, proposed_included)
        rebalance_result = _make_rebalance_result(
            proposed_version=proposed,
            proposed_members=proposed_members,
            previous_version_id=active.universe_version_id if active else None,
            candidate_version_id=candidate.universe_version_id,
            added=added,
            removed=removed,
            retained=retained,
            churn_pct=churn_pct,
        )
        return _make_service(
            active=active,
            candidates=[candidate],
            rebalance_result=rebalance_result,
        )

    def test_activates_proposed_version(self) -> None:
        active = _make_version(version_id="active-1", config_hash="old-hash")
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand-hash"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash="new-hash"
        )

        svc, version_repo, rebalance_repo, rotation_repo = self._build(
            active=active,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT", "GOOG"],
            added=["GOOG"],
            removed=[],
            retained=["AAPL", "MSFT"],
            churn_pct=0.05,
        )
        result = svc.rotate(candidate_version_id=candidate.universe_version_id, as_of=_T0)

        assert result.rotation_type == "scheduled"
        assert not result.skipped
        assert version_repo.activated_version_id == proposed.universe_version_id

    def test_retires_previous_active(self) -> None:
        active = _make_version(version_id="active-1", config_hash="old-hash")
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand-hash"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash="new-hash"
        )

        svc, version_repo, _, _ = self._build(
            active=active,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT"],
            added=["MSFT"],
            removed=[],
            retained=["AAPL"],
        )
        svc.rotate(candidate_version_id=candidate.universe_version_id, as_of=_T0)

        assert version_repo.retired_version is not None
        assert version_repo.retired_version.universe_version_id == active.universe_version_id
        assert version_repo.retired_version.status == UniverseStatus.RETIRED

    def test_persists_rotation_record(self) -> None:
        active = _make_version(version_id="active-1", config_hash="old-hash")
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand-hash"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash="new-hash"
        )

        svc, _, _, rotation_repo = self._build(
            active=active,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT"],
            added=["MSFT"],
            removed=[],
            retained=["AAPL"],
        )
        svc.rotate(candidate_version_id=candidate.universe_version_id, as_of=_T0)

        assert len(rotation_repo.inserted_records) == 1
        rec = rotation_repo.inserted_records[0]
        assert rec.rotation_type == "scheduled"
        assert rec.status == "completed"
        assert rec.new_universe_version_id == proposed.universe_version_id
        assert rec.previous_universe_version_id == active.universe_version_id

    def test_persists_rebalance_run(self) -> None:
        active = _make_version(version_id="active-1", config_hash="old-hash")
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand-hash"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash="new-hash"
        )

        svc, _, rebalance_repo, _ = self._build(
            active=active,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT"],
            added=["MSFT"],
            removed=[],
            retained=["AAPL"],
        )
        svc.rotate(candidate_version_id=candidate.universe_version_id, as_of=_T0)

        assert len(rebalance_repo.inserted_runs) == 1

    def test_skips_when_config_hash_unchanged(self) -> None:
        shared_hash = "same-hash-value"
        active = _make_version(version_id="active-1", config_hash=shared_hash)
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash=shared_hash
        )

        svc, version_repo, _, _ = self._build(
            active=active,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT"],
            added=[],
            removed=[],
            retained=["AAPL", "MSFT"],
        )
        result = svc.rotate(candidate_version_id=candidate.universe_version_id, as_of=_T0)

        assert result.skipped is True
        assert result.skip_reason is not None
        assert version_repo.activated_version_id is None

    def test_does_not_skip_when_force_rotation(self) -> None:
        shared_hash = "same-hash-value"
        active = _make_version(version_id="active-1", config_hash=shared_hash)
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash=shared_hash
        )

        svc, version_repo, _, _ = self._build(
            active=active,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT"],
            added=[],
            removed=[],
            retained=["AAPL", "MSFT"],
        )
        result = svc.rotate(
            candidate_version_id=candidate.universe_version_id,
            force_rotation=True,
            as_of=_T0,
        )

        assert not result.skipped
        assert version_repo.activated_version_id == proposed.universe_version_id

    def test_bootstrap_rotation_with_no_active(self) -> None:
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash="new"
        )

        svc, version_repo, _, rotation_repo = self._build(
            active=None,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT", "TSLA"],
            added=["AAPL", "MSFT", "TSLA"],
            removed=[],
            retained=[],
            churn_pct=1.0,
        )
        result = svc.rotate(candidate_version_id=candidate.universe_version_id, as_of=_T0)

        assert not result.skipped
        assert result.previous_version is None
        assert version_repo.activated_version_id == proposed.universe_version_id
        assert rotation_repo.inserted_records[0].previous_universe_version_id is None

    def test_raises_when_no_candidate(self) -> None:
        proposed = _make_version(version_id="prop-1", status=UniverseStatus.PROPOSED)
        rebalance_result = _make_rebalance_result(
            proposed_version=proposed,
            proposed_members=[],
            previous_version_id=None,
            candidate_version_id="cand-x",
            added=[],
            removed=[],
            retained=[],
        )
        svc, _, _, _ = _make_service(
            active=None,
            candidates=[],
            rebalance_result=rebalance_result,
        )
        with pytest.raises(RuntimeError, match="No candidate universe versions found"):
            svc.rotate(as_of=_T0)

    def test_rotation_record_includes_churn_and_symbols(self) -> None:
        active = _make_version(version_id="active-1", config_hash="old-hash")
        candidate = _make_version(
            version_id="cand-1", status=UniverseStatus.CANDIDATE, config_hash="cand"
        )
        proposed = _make_version(
            version_id="prop-1", status=UniverseStatus.PROPOSED, config_hash="new-hash"
        )

        svc, _, _, rotation_repo = self._build(
            active=active,
            candidate=candidate,
            proposed=proposed,
            proposed_included=["AAPL", "MSFT", "NVDA"],
            added=["NVDA"],
            removed=["FB"],
            retained=["AAPL", "MSFT"],
            churn_pct=0.10,
        )
        svc.rotate(candidate_version_id=candidate.universe_version_id, as_of=_T0)

        rec = rotation_repo.inserted_records[0]
        assert rec.churn_pct == pytest.approx(0.10)
        assert rec.added_symbols_json is not None
        assert rec.removed_symbols_json is not None
        assert rec.retained_symbols_json is not None
        assert "NVDA" in rec.added_symbols_json
        assert "FB" in rec.removed_symbols_json
        assert "AAPL" in rec.retained_symbols_json


# ---------------------------------------------------------------------------
# Tests: rollback()
# ---------------------------------------------------------------------------


class TestRollback:
    def _setup_rollback(
        self,
        *,
        active_symbols: list[str],
        target_symbols: list[str],
    ) -> tuple[_ServiceUnderTest, str, str, StubVersionRepo, StubRotationRepo]:
        active = _make_version(version_id="active-1", config_hash="hash-a")
        target = _make_version(
            version_id="target-1",
            status=UniverseStatus.RETIRED,
            config_hash="hash-old",
        )
        target_members = _make_members(target.universe_version_id, target_symbols)

        proposed_placeholder = _make_version(
            version_id="placeholder", status=UniverseStatus.PROPOSED
        )
        rebalance_result = _make_rebalance_result(
            proposed_version=proposed_placeholder,
            proposed_members=[],
            previous_version_id=None,
            candidate_version_id="cand",
            added=[],
            removed=[],
            retained=[],
        )
        version_repo = StubVersionRepo(
            active_version=active,
            versions={
                active.universe_version_id: active,
                target.universe_version_id: target,
            },
            members_by_id={target.universe_version_id: target_members},
        )
        rebalance_repo = StubRebalanceRepo()
        rotation_repo = StubRotationRepo()
        rebalance_svc = StubRebalanceService(result=rebalance_result)
        svc = _ServiceUnderTest(version_repo, rebalance_repo, rotation_repo, rebalance_svc)
        return (
            svc,
            active.universe_version_id,
            target.universe_version_id,
            version_repo,
            rotation_repo,
        )

    def test_creates_rollback_version_and_activates(self) -> None:
        svc, _, target_id, version_repo, _ = self._setup_rollback(
            active_symbols=["AAPL", "MSFT"],
            target_symbols=["AAPL", "TSLA"],
        )
        result = svc.rollback(
            target_version_id=target_id,
            rollback_reason="bad_data",
            as_of=_T1,
        )

        assert result.rotation_type == "rollback"
        assert version_repo.activated_version_id is not None
        rb_version = version_repo.versions[version_repo.activated_version_id]
        assert rb_version.rebalance_reason == "rollback:bad_data"

    def test_retires_current_active_on_rollback(self) -> None:
        svc, active_id, target_id, version_repo, _ = self._setup_rollback(
            active_symbols=["AAPL", "MSFT"],
            target_symbols=["AAPL", "TSLA"],
        )
        svc.rollback(target_version_id=target_id, rollback_reason="data_issue", as_of=_T1)

        assert version_repo.retired_version is not None
        assert version_repo.retired_version.universe_version_id == active_id

    def test_persists_rollback_rotation_record(self) -> None:
        svc, active_id, target_id, _, rotation_repo = self._setup_rollback(
            active_symbols=["AAPL"],
            target_symbols=["AAPL", "NVDA"],
        )
        svc.rollback(target_version_id=target_id, rollback_reason="ops_alert", as_of=_T1)

        assert len(rotation_repo.inserted_records) == 1
        rec = rotation_repo.inserted_records[0]
        assert rec.rotation_type == "rollback"
        assert rec.rollback_reason == "ops_alert"
        assert rec.status == "completed"
        assert rec.previous_universe_version_id == active_id
        assert rec.rebalance_run_id is None

    def test_rollback_version_copies_target_members(self) -> None:
        target_symbols = ["AAPL", "NVDA", "TSLA"]
        svc, _, target_id, version_repo, _ = self._setup_rollback(
            active_symbols=["AAPL", "MSFT"],
            target_symbols=target_symbols,
        )
        svc.rollback(target_version_id=target_id, rollback_reason="test", as_of=_T1)

        rb_id = version_repo.activated_version_id
        rb_members = [m for m in version_repo.inserted_members if m.universe_version_id == rb_id]
        rb_symbols = {m.symbol for m in rb_members if m.excluded_reason is None}
        assert rb_symbols == set(target_symbols)

    def test_rollback_raises_when_target_not_found(self) -> None:
        proposed = _make_version(version_id="prop", status=UniverseStatus.PROPOSED)
        rebalance_result = _make_rebalance_result(
            proposed_version=proposed,
            proposed_members=[],
            previous_version_id=None,
            candidate_version_id="cand",
            added=[],
            removed=[],
            retained=[],
        )
        svc, _, _, _ = _make_service(active=None, candidates=[], rebalance_result=rebalance_result)
        with pytest.raises(ValueError, match="not found"):
            svc.rollback(target_version_id="nonexistent-id", rollback_reason="test", as_of=_T1)

    def test_rollback_raises_for_empty_target(self) -> None:
        active = _make_version(version_id="active-1")
        target = _make_version(version_id="target-empty", status=UniverseStatus.RETIRED)
        proposed = _make_version(version_id="prop", status=UniverseStatus.PROPOSED)
        rebalance_result = _make_rebalance_result(
            proposed_version=proposed,
            proposed_members=[],
            previous_version_id=None,
            candidate_version_id="cand",
            added=[],
            removed=[],
            retained=[],
        )
        version_repo = StubVersionRepo(
            active_version=active,
            versions={
                active.universe_version_id: active,
                target.universe_version_id: target,
            },
            members_by_id={target.universe_version_id: []},
        )
        svc = _ServiceUnderTest(
            version_repo,
            StubRebalanceRepo(),
            StubRotationRepo(),
            StubRebalanceService(result=rebalance_result),
        )
        with pytest.raises(RotationSafetyError, match="no included members"):
            svc.rollback(
                target_version_id=target.universe_version_id,
                rollback_reason="test",
                as_of=_T1,
            )

    def test_approved_by_propagated_to_record(self) -> None:
        svc, _, target_id, _, rotation_repo = self._setup_rollback(
            active_symbols=["AAPL"],
            target_symbols=["AAPL", "NVDA"],
        )
        svc.rollback(
            target_version_id=target_id,
            rollback_reason="ops",
            approved_by="alice",
            as_of=_T1,
        )
        rec = rotation_repo.inserted_records[0]
        assert rec.approved_by == "alice"


# ---------------------------------------------------------------------------
# Tests: should_rotate() (job utility)
# ---------------------------------------------------------------------------


class TestShouldRotate:
    def test_daily_always_rotates(self) -> None:
        for weekday in range(7):
            # Build a monday-based offset
            dt = datetime(2025, 6, 2 + weekday, 9, 0, tzinfo=UTC)  # starts on Monday
            assert should_rotate(dt, "daily")

    def test_weekly_rotates_only_on_monday(self) -> None:
        monday = datetime(2025, 6, 2, 9, 0, tzinfo=UTC)
        assert should_rotate(monday, "weekly")

    def test_weekly_does_not_rotate_on_other_days(self) -> None:
        for day_offset in range(1, 7):
            dt = datetime(2025, 6, 2 + day_offset, 9, 0, tzinfo=UTC)
            assert not should_rotate(dt, "weekly")

    def test_invalid_cadence_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported cadence"):
            should_rotate(datetime(2025, 6, 2, tzinfo=UTC), "monthly")
