"""What Box A tells the worker to re-simulate each week (pre-launch plan 3.3a).

The weekly portfolio review judges every ACTIVE / ON_DECK / BENCH strategy on one shared
recent window. On the paper box that re-sim is the worker's job (Box B), so this service
publishes the inputs the worker needs and nothing else: the tracked strategies with their
stored research parameters, the shared window as ``resolve_bench_window`` builds it for
A's own bench review, the dataset version the worker should pull, and A's git SHA so the
worker can tell up front whether its result would pass the gate.
"""

from __future__ import annotations

import os
from datetime import date

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.platform_replay.bench_hooks import (
    resolve_bench_window,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.worker_results import (
    BenchResimInputs,
    BenchResimStrategyInput,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)
from autonomous_trading_platform.strategy.configs.stored_config import stored_config_parameters

logger = get_logger(__name__)

# The tiers the weekly review scores (PortfolioReviewService._REVIEWED_TIERS).
RESIM_TIERS: tuple[MembershipStatus, ...] = (
    MembershipStatus.ACTIVE,
    MembershipStatus.ON_DECK,
    MembershipStatus.BENCH,
)


class BenchResimInputsService:
    def __init__(
        self,
        session: Session,
        *,
        memberships: PortfolioMembershipRepository | None = None,
        settings_repo: OperatorSettingsRepository | None = None,
        local_git_sha: str | None = None,
    ) -> None:
        self._session = session
        self._memberships = memberships or PortfolioMembershipRepository(session)
        self._settings = settings_repo or OperatorSettingsRepository(session)
        self._git_sha = local_git_sha if local_git_sha is not None else os.getenv("GIT_SHA")

    def build(self, *, as_of: date, trading_days: int | None = None) -> BenchResimInputs:
        settings = self._settings.get_or_create_default()
        days = trading_days if trading_days is not None else int(settings.bench_resim_window_days)

        strategies = self._strategies()
        inputs = BenchResimInputs(
            as_of=as_of,
            git_sha=self._git_sha or None,
            trading_days=days,
            strategies=strategies,
        )

        window = resolve_bench_window(
            session=self._session, as_of=as_of, trading_days=days, replay_symbols=[]
        )
        if window is None:
            inputs.window_reason = "insufficient_data_for_window"
        else:
            inputs.window_start = window.start_date
            inputs.window_end = window.end_date
            inputs.dataset_version = window.dataset_version
            inputs.price_basis = window.price_basis.value
            inputs.symbols = list(window.symbols)

        logger.info(
            "bench_resim_inputs.built",
            extra={
                "as_of": as_of.isoformat(),
                "strategies": len(strategies),
                "window_reason": inputs.window_reason,
                "dataset_version": inputs.dataset_version,
            },
        )
        return inputs

    def _strategies(self) -> list[BenchResimStrategyInput]:
        rows = self._memberships.get_by_statuses([t.value for t in RESIM_TIERS])
        out: list[BenchResimStrategyInput] = []
        for row in rows:
            config = self._session.get(StrategyConfigs, row.strategy_id)
            if config is None:
                logger.warning(
                    "bench_resim_inputs.missing_strategy_config",
                    extra={"strategy_id": row.strategy_id, "tier": row.status},
                )
                continue
            out.append(
                BenchResimStrategyInput(
                    strategy_id=row.strategy_id,
                    tier=row.status,
                    strategy_type=config.strategy_type,
                    config_hash=config.config_hash,
                    parameters=stored_config_parameters(config.config_json),
                )
            )
        return out
