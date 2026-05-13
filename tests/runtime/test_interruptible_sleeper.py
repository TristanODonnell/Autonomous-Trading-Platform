from __future__ import annotations

import signal
import threading
import time

import pytest

from autonomous_trading_platform.runtime.interruptible_sleep import InterruptibleSleeper


def test_is_shutdown_starts_false() -> None:
    sleeper = InterruptibleSleeper()
    assert sleeper.is_shutdown is False


def test_request_shutdown_sets_flag() -> None:
    sleeper = InterruptibleSleeper()
    sleeper.request_shutdown()
    assert sleeper.is_shutdown is True


def test_sleep_returns_immediately_when_already_shutdown() -> None:
    sleeper = InterruptibleSleeper()
    sleeper.request_shutdown()
    start = time.monotonic()
    sleeper.sleep(10.0)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0


def test_sleep_completes_naturally_for_short_duration() -> None:
    sleeper = InterruptibleSleeper()
    start = time.monotonic()
    sleeper.sleep(0.1)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05


def test_sleep_is_interrupted_early_by_request_shutdown() -> None:
    sleeper = InterruptibleSleeper()

    def _stopper() -> None:
        time.sleep(0.15)
        sleeper.request_shutdown()

    t = threading.Thread(target=_stopper, daemon=True)
    t.start()
    start = time.monotonic()
    sleeper.sleep(30.0)
    t.join(timeout=3.0)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert sleeper.is_shutdown is True


def test_sleep_zero_or_negative_returns_immediately() -> None:
    sleeper = InterruptibleSleeper()
    start = time.monotonic()
    sleeper.sleep(0)
    sleeper.sleep(-1.0)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0


def test_install_signal_handlers_registers_sigint_and_calls_request_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: dict[int, object] = {}

    def fake_signal(sig: int, handler: object) -> None:
        registered[sig] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    sleeper = InterruptibleSleeper()
    sleeper.install_signal_handlers(label="TestRunner")

    assert signal.SIGINT in registered
    handler = registered[signal.SIGINT]
    assert callable(handler)

    handler(signal.SIGINT, None)  # type: ignore[call-arg]
    assert sleeper.is_shutdown is True
