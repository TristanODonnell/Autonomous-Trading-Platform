from __future__ import annotations

import contextlib
import signal
import time


class InterruptibleSleeper:
    """
    Reusable shutdown flag + sleep utility for long-running scheduler loops.

    Usage:
        sleeper = InterruptibleSleeper()
        sleeper.install_signal_handlers()   # wire SIGINT / SIGTERM

        while not sleeper.is_shutdown:
            do_work()
            sleeper.sleep(300)              # wakes every 1s to check shutdown
    """

    def __init__(self) -> None:
        self._shutdown = False

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown

    def request_shutdown(self) -> None:
        self._shutdown = True

    def sleep(self, seconds: float) -> None:
        """Sleep for up to *seconds*, waking every 1 s to check shutdown."""
        end = time.monotonic() + seconds
        while not self._shutdown:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))

    def install_signal_handlers(self, *, label: str = "Scheduler") -> None:
        """Register SIGINT and SIGTERM to call request_shutdown().

        *label* is used only in the shutdown message printed to stdout.
        OSError/ValueError are suppressed so this is safe to call from
        non-main threads or restricted environments.
        """

        def _handler(signum: int, _frame: object) -> None:
            name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            print(f"\n[{label}] Received {name}, shutting down gracefully...")
            self.request_shutdown()

        with contextlib.suppress(OSError, ValueError):
            signal.signal(signal.SIGINT, _handler)
        with contextlib.suppress(OSError, ValueError):
            signal.signal(signal.SIGTERM, _handler)
