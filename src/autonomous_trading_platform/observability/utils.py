from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter


@contextmanager
def record_duration(callback: Callable[[float], None]) -> Iterator[None]:
    start = perf_counter()
    try:
        yield
    finally:
        callback(perf_counter() - start)
