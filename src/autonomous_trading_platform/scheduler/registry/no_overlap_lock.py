from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InMemoryNoOverlapLock:
    _active_locks: set[str] = field(default_factory=set)

    def acquire(self, lock_key: str) -> bool:
        if lock_key in self._active_locks:
            return False

        self._active_locks.add(lock_key)
        return True

    def release(self, lock_key: str) -> None:
        self._active_locks.discard(lock_key)
