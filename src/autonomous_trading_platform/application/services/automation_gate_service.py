"""One answer to "may this automation act right now?" (pre-launch plan 3.1).

Every automated decision path (auto-promotion, auto-demotion, allocation rebalance, the
weekly review's swaps, accepting worker results) asks this gate before changing anything.
Two layers, both operator-controlled without a deploy:

1. The **kill switch** (``atp safety enable-kill-switch``) overrides everything: while it
   is on, no automation acts, whatever its own switch says.
2. A **per-automation switch** in ``operator_settings`` (``atp settings set`` / the settings
   API): ``auto_promote_enabled``, ``auto_demote_on_breach``, ``auto_rebalance_enabled``,
   ``portfolio_review_mode == auto`` and ``worker_results_enabled``.

The services keep their own switch checks (and their audit rows for "skipped because
disabled"); this gate adds the kill-switch layer and a single status view for the CLI and
dashboards.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.observability import metrics as _metrics
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

logger = get_logger(__name__)

KILL_SWITCH_REASON = "kill_switch_enabled"


class Automation(enum.StrEnum):
    PROMOTION = "promotion"
    DEMOTION = "demotion"
    REBALANCE = "rebalance"
    REVIEW_SWAPS = "review_swaps"
    WORKER_RESULTS = "worker_results"


# Which operator_settings column (and value) turns each automation on.
_SWITCH: dict[Automation, tuple[str, Any]] = {
    Automation.PROMOTION: ("auto_promote_enabled", True),
    Automation.DEMOTION: ("auto_demote_on_breach", True),
    Automation.REBALANCE: ("auto_rebalance_enabled", True),
    Automation.REVIEW_SWAPS: ("portfolio_review_mode", "auto"),
    Automation.WORKER_RESULTS: ("worker_results_enabled", True),
}


@dataclass(frozen=True)
class AutomationDecision:
    automation: Automation
    allowed: bool
    reason: str | None  # None when allowed
    kill_switch_enabled: bool
    switch_enabled: bool
    switch_name: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "automation": self.automation.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "kill_switch_enabled": self.kill_switch_enabled,
            "switch": self.switch_name,
            "switch_enabled": self.switch_enabled,
        }


class AutomationGateService:
    def __init__(
        self,
        session: Session,
        *,
        kill_switch_repo: KillSwitchStateRepository | None = None,
        settings_repo: OperatorSettingsRepository | None = None,
    ) -> None:
        self._kill_switch = kill_switch_repo or KillSwitchStateRepository(session)
        self._settings = settings_repo or OperatorSettingsRepository(session)

    def kill_switch_enabled(self) -> bool:
        return bool(self._kill_switch.get_current_state().is_enabled)

    def check(self, automation: Automation) -> AutomationDecision:
        column, on_value = _SWITCH[automation]
        row = self._settings.get_or_create_default()
        switch_enabled = getattr(row, column, None) == on_value
        kill = self.kill_switch_enabled()
        if kill:
            reason: str | None = KILL_SWITCH_REASON
        elif not switch_enabled:
            reason = f"{column}_off"
        else:
            reason = None
        decision = AutomationDecision(
            automation=automation,
            allowed=reason is None,
            reason=reason,
            kill_switch_enabled=kill,
            switch_enabled=switch_enabled,
            switch_name=column,
        )
        if not decision.allowed:
            _metrics.automation_blocked_total.add(
                1, {"automation": automation.value, "reason": reason or "unknown"}
            )
            logger.info("automation_gate.blocked", extra=decision.as_dict())
        return decision

    def blocked_by_kill_switch(self, automation: Automation) -> bool:
        """True when the kill switch alone would stop this automation (switch ignored)."""
        if not self.kill_switch_enabled():
            return False
        _metrics.automation_blocked_total.add(
            1, {"automation": automation.value, "reason": KILL_SWITCH_REASON}
        )
        logger.warning(
            "automation_gate.kill_switch_override", extra={"automation": automation.value}
        )
        return True

    def status(self) -> dict[str, Any]:
        """Every automation's effective state, for `atp controls automation` and dashboards."""
        return {
            "kill_switch_enabled": self.kill_switch_enabled(),
            "automations": {a.value: self.check(a).as_dict() for a in Automation},
        }
