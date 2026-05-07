import pytest

from autonomous_trading_platform.scheduler.registry.manual_trigger_service import (
    ManualTriggerService,
)
from autonomous_trading_platform.scheduler.registry.no_overlap_lock import (
    InMemoryNoOverlapLock,
)


def test_manual_trigger_dispatches_registered_job():
    calls = []

    service = ManualTriggerService(
        dispatchers={
            "market_ingestion_cycle": lambda: calls.append("market_ingestion_cycle"),
        }
    )

    result = service.trigger("market_ingestion_cycle")

    assert calls == ["market_ingestion_cycle"]
    assert result.job_name == "market_ingestion_cycle"
    assert result.status == "completed"


def test_manual_trigger_rejects_unknown_job():
    service = ManualTriggerService(dispatchers={})

    with pytest.raises(ValueError, match="Unknown scheduler job"):
        service.trigger("not_a_real_job")


def test_manual_trigger_rejects_missing_dispatcher():
    service = ManualTriggerService(dispatchers={})

    with pytest.raises(ValueError, match="No dispatcher registered"):
        service.trigger("market_ingestion_cycle")


def test_manual_trigger_skips_duplicate_run_when_lock_is_active():
    calls = []
    lock = InMemoryNoOverlapLock()

    assert lock.acquire("scheduler:market_ingestion_cycle") is True

    service = ManualTriggerService(
        dispatchers={
            "market_ingestion_cycle": lambda: calls.append("market_ingestion_cycle"),
        },
        lock=lock,
    )

    result = service.trigger("market_ingestion_cycle")

    assert calls == []
    assert result.job_name == "market_ingestion_cycle"
    assert result.status == "skipped"
    assert result.result == {"reason": "already_running"}


def test_manual_trigger_releases_lock_after_successful_run():
    calls = []
    lock = InMemoryNoOverlapLock()

    service = ManualTriggerService(
        dispatchers={
            "market_ingestion_cycle": lambda: calls.append("market_ingestion_cycle"),
        },
        lock=lock,
    )

    first_result = service.trigger("market_ingestion_cycle")
    second_result = service.trigger("market_ingestion_cycle")

    assert calls == ["market_ingestion_cycle", "market_ingestion_cycle"]
    assert first_result.status == "completed"
    assert second_result.status == "completed"
