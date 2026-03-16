from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class KillSwitchState:
    enabled: bool
    reason: str | None
    updated_by: str | None
    updated_at: datetime | None
