# storage/sor/models/portfolio_drawdown_governance_state.py

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.storage.sor.models.base import Base

GOVERNANCE_SINGLETON_ID = "current"


class PortfolioDrawdownGovernanceState(Base):
    """
    Singleton SOR record that persists the portfolio drawdown governance state
    across scheduler restarts, deploys, and failovers.

    The row with id="current" is the authoritative live state.  All fields are
    deliberately nullable so the row can be created with safe defaults before
    any equity data has been observed.

    Peak equity is the lifetime high-watermark: it is updated upward as equity
    grows and never reset downward (except via operator acknowledgement reset).
    This means a restart cannot cause a spurious breach by comparing fresh
    equity against a reset peak.
    """

    __tablename__ = "portfolio_drawdown_governance_state"

    governance_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=GOVERNANCE_SINGLETON_ID,
    )

    # High-watermark equity in USD.  None until the first evaluate() call.
    peak_equity_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Governance enforcement flags.
    governance_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rebalancing_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Breach context — populated when a breach fires; cleared on acknowledgement.
    breach_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    breach_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    breach_threshold_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_action: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Whether the global kill switch was automatically activated during this breach.
    kill_switch_auto_activated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Operator acknowledgement — set when an operator explicitly clears the breach.
    operator_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operator_acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Configured recovery mode at breach time.
    recovery_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
