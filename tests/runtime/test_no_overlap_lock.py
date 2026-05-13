from __future__ import annotations

from autonomous_trading_platform.scheduler.registry.no_overlap_lock import InMemoryNoOverlapLock


def test_first_acquire_succeeds() -> None:
    lock = InMemoryNoOverlapLock()
    assert lock.acquire("job_a") is True


def test_second_acquire_for_same_key_fails() -> None:
    """Duplicate execution is blocked: a held lock cannot be re-acquired."""
    lock = InMemoryNoOverlapLock()
    lock.acquire("job_a")
    assert lock.acquire("job_a") is False


def test_release_allows_reacquire() -> None:
    lock = InMemoryNoOverlapLock()
    lock.acquire("job_a")
    lock.release("job_a")
    assert lock.acquire("job_a") is True


def test_release_of_unheld_key_is_no_op() -> None:
    lock = InMemoryNoOverlapLock()
    lock.release("never_acquired")  # must not raise
    assert lock.acquire("never_acquired") is True


def test_different_keys_are_independent() -> None:
    lock = InMemoryNoOverlapLock()
    lock.acquire("job_a")
    assert lock.acquire("job_b") is True


def test_releasing_one_key_does_not_affect_another() -> None:
    lock = InMemoryNoOverlapLock()
    lock.acquire("job_a")
    lock.acquire("job_b")
    lock.release("job_a")

    assert lock.acquire("job_a") is True
    assert lock.acquire("job_b") is False
