"""Trading-cycle step: apply due corporate actions before anything else trades.

Runs at the top of the evaluation job on every cycle (cheap when nothing is due): the
first cycle on or after an ex-date rewrites the sleeves (and, in backtests, the
account book) through the shared rule, so the sleeve totals already match the
broker's post-split account and the unowned-share adoption that follows has nothing
to adopt (plan 5d, decision D4).

If the step fails, the cycle keeps trading but adoption is switched off for the
cycle: adopting after a failed split application is exactly the path that sold
post-split shares out of the unattributed sleeve.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from autonomous_trading_platform.execution.clients.simulated_broker_client import (
    SimulatedBrokerClient,
)
from autonomous_trading_platform.execution.services.corporate_action_accounting_service import (
    CorporateActionAccountingService,
    CorporateActionApplicationReport,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

PriceProvider = Callable[[list[str]], Mapping[str, Decimal | float]]


@dataclass
class CorporateActionStepOutcome:
    report: CorporateActionApplicationReport | None
    failed: bool = False
    error: str | None = None
    # Symbols the cycle must not adopt unowned shares in this cycle.
    skip_adoption_symbols: set[str] = field(default_factory=set)
    # False when the step failed: no adoption at all this cycle.
    adoption_allowed: bool = True

    def summary(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "failed": self.failed,
            "error": self.error,
            "adoption_allowed": self.adoption_allowed,
            "skip_adoption_symbols": sorted(self.skip_adoption_symbols),
        }
        if self.report is not None:
            base.update(self.report.summary())
        return base


def _audit(audit_logger: Any | None, **event: Any) -> None:
    if audit_logger is not None and hasattr(audit_logger, "record_event"):
        audit_logger.record_event(**event)


def apply_due_corporate_actions(
    *,
    session: Session,
    now_utc: datetime,
    broker_client: Any,
    run_id: UUID | None,
    price_provider: PriceProvider | None = None,
    audit_logger: Any | None = None,
    service: CorporateActionAccountingService | None = None,
) -> CorporateActionStepOutcome:
    """Apply every stored action due by ``now_utc`` to the books. Never raises."""
    # Only the simulated broker's account lives in our snapshots; a real broker has
    # already applied the action to the account it reports.
    adjust_account_book = isinstance(broker_client, SimulatedBrokerClient)
    accounting = service or CorporateActionAccountingService()
    try:
        with SorUnitOfWork(session) as uow:
            report = accounting.apply_due_actions(
                uow,
                as_of=now_utc.date(),
                timestamp=now_utc,
                price_provider=price_provider,
                adjust_account_book=adjust_account_book,
                run_id=run_id,
            )
    except Exception as exc:  # pragma: no cover - exercised through the failure test
        logger.error(
            "corporate_actions.step_failed",
            extra={"run_id": str(run_id), "error": str(exc)},
            exc_info=True,
        )
        _audit(
            audit_logger,
            run_id=str(run_id),
            event_type="CORPORATE_ACTIONS_STEP_FAILED",
            component="scheduler.jobs.apply_corporate_actions_step",
            message="corporate action application failed; adoption disabled this cycle",
            metadata={"error": str(exc), "severity": "error"},
        )
        return CorporateActionStepOutcome(
            report=None, failed=True, error=str(exc), adoption_allowed=False
        )

    if report.applied or report.skipped:
        _audit(
            audit_logger,
            run_id=str(run_id),
            event_type="CORPORATE_ACTIONS_APPLIED",
            component="scheduler.jobs.apply_corporate_actions_step",
            message=(
                f"{report.applied_count} corporate action application(s), "
                f"{len(report.skipped)} skipped"
            ),
            metadata={**report.summary(), "severity": "info"},
        )
    return CorporateActionStepOutcome(
        report=report,
        skip_adoption_symbols=set(report.pending_symbols),
    )
