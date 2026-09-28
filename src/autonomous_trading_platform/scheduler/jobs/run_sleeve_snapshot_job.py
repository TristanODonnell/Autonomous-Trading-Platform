# autonomous_trading_platform/scheduler/jobs/run_sleeve_snapshot_job.py
"""
End-of-cycle sleeve bookkeeping (portfolio mode only).

Values every sleeve at current prices (per-strategy P&L history) and checks the
attribution invariant: sleeves summed per symbol must equal the broker account.
Report-only — mismatches are logged, never auto-corrected here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from autonomous_trading_platform.contracts.accounting.strategy_sleeve import (
    SleeveReconciliationReport,
)
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    StrategySleeveLedgerService,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    TradingCycleDependencies,
)
from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
    _fetch_positions,
    _fetch_prices,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)


def run_sleeve_snapshot_job(
    *,
    trading_cycle_dependencies: TradingCycleDependencies,
    run_id: UUID,
    now_utc: datetime,
) -> SleeveReconciliationReport | None:
    deps = trading_cycle_dependencies
    if deps.strategy_runtimes is None:
        return None

    broker_client = deps.execution_context.broker_client
    ledger = StrategySleeveLedgerService()
    budgets = {runtime.strategy_id: runtime.budget_pct for runtime in deps.strategy_runtimes}
    total_capital = Decimal(str(deps.portfolio_engine.total_capital))
    account_positions = _fetch_positions(broker_client)

    with SorUnitOfWork(deps.session) as uow:
        sleeves = ledger.all_positions(uow)
        symbols = sorted({symbol for held in sleeves.values() for symbol in held})
        prices = _fetch_prices(broker_client, symbols)
        for strategy_id in sorted(set(budgets) | set(sleeves)):
            ledger.snapshot(
                uow,
                strategy_id=strategy_id,
                prices=prices,
                timestamp=now_utc,
                run_id=run_id,
                allocated_capital=budgets.get(strategy_id, Decimal("0")) * total_capital,
            )
        report = ledger.reconcile(uow, account_positions=account_positions, timestamp=now_utc)

    if not report.is_balanced:
        logger.warning(
            "sleeve_reconciliation.mismatch",
            extra={
                "run_id": str(run_id),
                "mismatches": {m.symbol: str(m.difference) for m in report.mismatches},
            },
        )
    return report
