# storage/sor/repositories/core/operator_settings_repository.py

from typing import cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.operator_settings import (
    OperatorSettingsRow,
)

DEFAULT_OPERATOR_SETTINGS_ID = "default"


class OperatorSettingsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_current(self) -> OperatorSettingsRow | None:
        return cast(
            OperatorSettingsRow | None,
            self._session.get(OperatorSettingsRow, DEFAULT_OPERATOR_SETTINGS_ID),
        )

    def get_or_create_default(self) -> OperatorSettingsRow:
        row = self.get_current()
        if row is not None:
            return row

        row = OperatorSettingsRow(
            settings_id=DEFAULT_OPERATOR_SETTINGS_ID,
            risk_tolerance="medium",
            max_drawdown_limit=0.10,
            max_strategy_drawdown=0.12,
            rebalance_frequency="weekly",
            auto_promote_enabled=False,
            min_sharpe_for_promotion=1.5,
            min_paper_trading_period_days=30,
            auto_demote_on_breach=True,
            notify_drawdown_alerts=True,
            notify_strategy_promotion_events=True,
            notify_pipeline_failures=True,
            per_strategy_cap=0.25,
            target_portfolio_volatility=0.15,
            slippage_model="fixed",
            transaction_cost_model="per_share",
        )
        self._session.add(row)
        self._session.flush()
        self._session.commit()
        return row

    def update_current(self, values: dict, updated_by: str | None) -> OperatorSettingsRow:
        row = self.get_or_create_default()

        for key, value in values.items():
            setattr(row, key, value)

        row.updated_by = updated_by

        self._session.add(row)
        self._session.flush()
        self._session.commit()
        return row
