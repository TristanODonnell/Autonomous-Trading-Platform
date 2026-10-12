from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.worker_result_gate_service import (
    WorkerResultGateService,
)
from autonomous_trading_platform.contracts.governance.worker_results import (
    WorkerResultStatus,
    WorkerResultSubmission,
    WorkerResultType,
)
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.worker_result_submissions_repository import (
    WorkerResultSubmissionsRepository,
)

NOW = datetime(2026, 10, 16, 23, 30, tzinfo=UTC)  # Friday night ET
LOCAL_SHA = "0f71e8627abcdef0123456789abcdef012345678"


def _submission(**overrides) -> WorkerResultSubmission:
    base = dict(
        run_id="six_month_2026w42",
        result_type=WorkerResultType.BENCH_RESIMS,
        produced_at=NOW,
        git_sha=LOCAL_SHA,
        worker_host="box-b",
        fixture="fixtures/platform/replays/long/six_month_regression.yaml",
        dataset_as_of=date(2026, 10, 15),
        dataset_versions=["raw_bars_20261015"],
        payload={"scorecards": [{"strategy_id": "momentum_v1", "score": 1.2}]},
    )
    base.update(overrides)
    return WorkerResultSubmission(**base)


def _enable(session: Session, **extra) -> None:
    OperatorSettingsRepository(session).update_current(
        {"worker_results_enabled": True, **extra}, updated_by="test"
    )


def _gate(session: Session, sha: str | None = LOCAL_SHA) -> WorkerResultGateService:
    return WorkerResultGateService(session, local_git_sha=sha)


def test_rejected_while_the_switch_is_off(db_session: Session) -> None:
    decision = _gate(db_session).evaluate(_submission(), now=NOW)

    assert decision.status == WorkerResultStatus.REJECTED
    assert decision.reason == "worker_results_disabled"
    row = WorkerResultSubmissionsRepository(db_session).get("six_month_2026w42")
    assert row is not None and row.status == "rejected"
    assert row.rejection_reason == "worker_results_disabled"
    assert row.payload == {"scorecards": [{"strategy_id": "momentum_v1", "score": 1.2}]}


def test_accepted_when_switch_on_fresh_and_same_code(db_session: Session) -> None:
    _enable(db_session)

    decision = _gate(db_session).evaluate(_submission(), now=NOW)

    assert decision.status == WorkerResultStatus.ACCEPTED
    assert decision.reason is None
    assert decision.dataset_age_days == 1
    assert decision.duplicate is False
    row = WorkerResultSubmissionsRepository(db_session).get("six_month_2026w42")
    assert row is not None
    assert row.status == "accepted" and row.rejection_reason is None
    assert row.dataset_as_of == date(2026, 10, 15)
    assert row.git_sha == LOCAL_SHA and row.worker_host == "box-b"
    assert row.received_at == NOW
    latest = WorkerResultSubmissionsRepository(db_session).latest_accepted("bench_resims")
    assert latest is not None and latest.run_id == "six_month_2026w42"


def test_kill_switch_rejects_even_with_the_switch_on(db_session: Session) -> None:
    _enable(db_session)
    KillSwitchStateRepository(db_session).enable(reason="x", updated_by="ops", updated_at=NOW)

    decision = _gate(db_session).evaluate(_submission(), now=NOW)

    assert decision.status == WorkerResultStatus.REJECTED
    assert decision.reason == "kill_switch_enabled"


def test_dataset_age_limit_is_inclusive(db_session: Session) -> None:
    _enable(db_session, worker_result_max_age_days=3)
    gate = _gate(db_session)

    at_limit = gate.evaluate(_submission(run_id="r1", dataset_as_of=date(2026, 10, 13)), now=NOW)
    too_old = gate.evaluate(_submission(run_id="r2", dataset_as_of=date(2026, 10, 12)), now=NOW)
    missing = gate.evaluate(_submission(run_id="r3", dataset_as_of=None), now=NOW)

    assert at_limit.status == WorkerResultStatus.ACCEPTED and at_limit.dataset_age_days == 3
    assert too_old.reason == "stale_dataset" and too_old.dataset_age_days == 4
    assert missing.reason == "missing_dataset_as_of" and missing.dataset_age_days is None


def test_max_age_zero_means_same_session_only(db_session: Session) -> None:
    _enable(db_session, worker_result_max_age_days=0)

    decision = _gate(db_session).evaluate(_submission(dataset_as_of=date(2026, 10, 16)), now=NOW)

    assert decision.status == WorkerResultStatus.ACCEPTED


def test_git_sha_rules(db_session: Session) -> None:
    _enable(db_session)
    gate = _gate(db_session)

    assert (
        gate.evaluate(_submission(run_id="r1", git_sha=None), now=NOW).reason == "missing_git_sha"
    )
    assert (
        gate.evaluate(_submission(run_id="r2", git_sha="deadbeefdeadbeef"), now=NOW).reason
        == "git_sha_mismatch"
    )
    # short SHA of the same commit is fine
    assert gate.evaluate(_submission(run_id="r3", git_sha=LOCAL_SHA[:9]), now=NOW).reason is None
    # no local SHA known (dev box) → cannot compare, accept
    assert (
        _gate(db_session, sha=None)
        .evaluate(_submission(run_id="r4", git_sha="abc1234"), now=NOW)
        .reason
        is None
    )

    OperatorSettingsRepository(db_session).update_current(
        {"worker_result_require_matching_git_sha": False}, updated_by="test"
    )
    assert gate.evaluate(_submission(run_id="r5", git_sha="other123"), now=NOW).reason is None
    assert gate.evaluate(_submission(run_id="r6", git_sha=None), now=NOW).reason is None


def test_empty_payload_is_rejected(db_session: Session) -> None:
    _enable(db_session)

    decision = _gate(db_session).evaluate(_submission(payload={}), now=NOW)

    assert decision.reason == "empty_payload"


def test_same_run_id_returns_the_first_decision(db_session: Session) -> None:
    gate = _gate(db_session)
    first = gate.evaluate(_submission(), now=NOW)  # rejected: switch off
    assert first.status == WorkerResultStatus.REJECTED
    _enable(db_session)

    again = gate.evaluate(_submission(), now=NOW)

    assert again.duplicate is True
    assert again.status == WorkerResultStatus.REJECTED
    assert again.reason == "worker_results_disabled"
    assert again.received_at == NOW
    assert len(WorkerResultSubmissionsRepository(db_session).list_since(NOW)) == 1


def test_mark_applied_and_list_since(db_session: Session) -> None:
    _enable(db_session)
    _gate(db_session).evaluate(_submission(run_id="a"), now=NOW)
    _gate(db_session).evaluate(_submission(run_id="b", payload={}), now=NOW)  # rejected
    repo = WorkerResultSubmissionsRepository(db_session)

    repo.mark_applied("a", applied_at=NOW, review_id="review_20261017T000000")

    row = repo.get("a")
    assert row is not None and row.status == "applied" and row.review_id == "review_20261017T000000"
    assert [r.run_id for r in repo.list_since(NOW, status="rejected")] == ["b"]
    latest = repo.latest_accepted("bench_resims")
    assert latest is not None and latest.run_id == "a"  # applied still counts
    with pytest.raises(KeyError):
        repo.mark_applied("missing", applied_at=NOW, review_id=None)
