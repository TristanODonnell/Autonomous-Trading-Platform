# application/services/operator_settings_service.py

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


@dataclass(frozen=True)
class OperatorSettingsDTO:
    risk_tolerance: str
    max_drawdown_limit: float
    max_strategy_drawdown: float
    rebalance_frequency: str
    auto_promote_enabled: bool
    auto_rebalance_enabled: bool
    min_sharpe_for_promotion: float
    min_paper_trading_period_days: int
    auto_demote_on_breach: bool
    notify_drawdown_alerts: bool
    notify_strategy_promotion_events: bool
    notify_pipeline_failures: bool
    per_strategy_cap: float
    target_portfolio_volatility: float
    slippage_model: str
    transaction_cost_model: str
    max_total_strategy_allocation_pct: float


class OperatorSettingsService:
    def __init__(
        self,
        settings_repo: OperatorSettingsRepository,
        audit_log_repo,
    ) -> None:
        self._settings_repo = settings_repo
        self._audit_log_repo = audit_log_repo

    def get_settings(self) -> OperatorSettingsDTO:
        row = self._settings_repo.get_or_create_default()
        return self._to_dto(row)

    def update_settings(
        self,
        updates: dict,
        *,
        actor_user_id: str,
        reason: str | None = None,
    ) -> OperatorSettingsDTO:
        current = self._settings_repo.get_or_create_default()
        before = asdict(self._to_dto(current))

        clean_updates = {key: value for key, value in updates.items() if value is not None}

        updated = self._settings_repo.update_current(
            clean_updates,
            updated_by=actor_user_id,
        )

        after = asdict(self._to_dto(updated))

        changed = {
            key: {
                "previous": before[key],
                "new": after[key],
            }
            for key in clean_updates
            if before.get(key) != after.get(key)
        }

        if changed:
            self._audit_log_repo.record_operator_action(
                action="OPERATOR_SETTINGS_UPDATED",
                actor=actor_user_id,
                reason=reason or "settings update",
                occurred_at=datetime.now(UTC),
                component="settings",
                metadata={
                    "changes": changed,
                },
            )

        return self._to_dto(updated)

    def get_risk_profile(self) -> dict:
        settings = self.get_settings()

        drawdown_pct = round(settings.max_drawdown_limit * 100, 2)
        summary = (
            f"Your system will pause or restrict strategy promotion if drawdown exceeds "
            f"{drawdown_pct}%. Auto-promotion is "
            f"{'on' if settings.auto_promote_enabled else 'off'}."
        )

        bullets = [
            f"Risk tolerance is set to {settings.risk_tolerance}.",
            f"Maximum drawdown limit is {drawdown_pct}%.",
            f"Rebalance frequency is {settings.rebalance_frequency}.",
            "Allocation caps come from capital allocation policies and active overrides.",
        ]

        return {
            "summary": summary,
            "bullets": bullets,
        }

    def _to_dto(self, row) -> OperatorSettingsDTO:
        return OperatorSettingsDTO(
            risk_tolerance=row.risk_tolerance,
            max_drawdown_limit=float(row.max_drawdown_limit),
            max_strategy_drawdown=float(row.max_strategy_drawdown),
            rebalance_frequency=row.rebalance_frequency,
            auto_promote_enabled=row.auto_promote_enabled,
            auto_rebalance_enabled=getattr(row, "auto_rebalance_enabled", False),
            min_sharpe_for_promotion=float(row.min_sharpe_for_promotion),
            min_paper_trading_period_days=int(row.min_paper_trading_period_days),
            auto_demote_on_breach=row.auto_demote_on_breach,
            notify_drawdown_alerts=row.notify_drawdown_alerts,
            notify_strategy_promotion_events=row.notify_strategy_promotion_events,
            notify_pipeline_failures=row.notify_pipeline_failures,
            per_strategy_cap=float(row.per_strategy_cap),
            target_portfolio_volatility=float(row.target_portfolio_volatility),
            slippage_model=row.slippage_model or "fixed",
            transaction_cost_model=row.transaction_cost_model or "per_share",
            max_total_strategy_allocation_pct=float(
                getattr(row, "max_total_strategy_allocation_pct", 1.0) or 1.0
            ),
        )
