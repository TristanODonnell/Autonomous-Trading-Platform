"""Initial state seeding hook for platform backtest replay.

Applies the `initial_state` block from the fixture YAML to the database before
the first tick runs. Without this, the DB starts empty and governance/trading
evaluates zero strategies.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

# Fixture state name → DB state string mapping.
# Fixture YAML uses descriptive aliases; we canonicalize to the long-form DB strings
# used everywhere in services and queries (NOT the GovernanceState enum short-form values).
_STATE_MAP: dict[str, str] = {
    "paper_trading_active": "approved_for_paper_trading",
    "approved_for_paper_trading": "approved_for_paper_trading",
    "approved_paper": "approved_for_paper_trading",
    "approved_research": "approved_research",
    "approved_live": "approved_for_live_trading",
    "approved_for_live_trading": "approved_for_live_trading",
    "proposed": "proposed",
    "rejected": "rejected",
    "retired": "retired",
}


def apply_initial_state(
    *,
    session: Session,
    initial_state: Any,
    timestamp: datetime,
    actor: str = "platform_backtest",
) -> dict[str, Any]:
    """Apply the fixture's initial_state block to the database.

    Idempotent: existing rows are not overwritten. Returns a summary of
    what was created.
    """
    now = timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    summary: dict[str, Any] = {
        "settings_applied": False,
        "governance_rows_created": 0,
        "allocation_overrides_created": 0,
        "errors": [],
    }

    # 1. Operator settings
    settings_patch = getattr(initial_state, "settings", {}) or {}
    if settings_patch:
        try:
            _apply_operator_settings(session=session, patch=settings_patch, now=now)
            summary["settings_applied"] = True
        except Exception as exc:
            summary["errors"].append(f"settings: {exc}")

    # 2. Governance state + strategy_configs for each strategy
    governance_entries = getattr(initial_state, "governance", []) or []
    for entry in governance_entries:
        strategy_id = getattr(entry, "strategy_id", None) or entry.get("strategy_id")
        raw_state = getattr(entry, "state", None) or entry.get("state", "approved_paper")
        if not strategy_id:
            continue
        try:
            _upsert_strategy_governance(
                session=session,
                strategy_id=strategy_id,
                state=_STATE_MAP.get(str(raw_state), str(raw_state)),
                actor=actor,
                now=now,
            )
            summary["governance_rows_created"] += 1
        except Exception as exc:
            summary["errors"].append(f"governance[{strategy_id}]: {exc}")
        try:
            _upsert_strategy_config(
                session=session,
                strategy_id=strategy_id,
                now=now,
            )
        except Exception as exc:
            summary["errors"].append(f"strategy_config[{strategy_id}]: {exc}")

    # 3. Reset evaluation checkpoints so a fresh backtest run doesn't inherit
    #    last_evaluated_bar_timestamp from a prior run.
    for entry in governance_entries:
        strategy_id = getattr(entry, "strategy_id", None) or entry.get("strategy_id")
        if not strategy_id:
            continue
        try:
            _reset_strategy_checkpoint(session=session, strategy_id=strategy_id)
        except Exception as exc:
            summary["errors"].append(f"checkpoint_reset[{strategy_id}]: {exc}")

    # 4. Per-strategy allocation overrides
    allocation_entries = getattr(initial_state, "allocations", []) or []
    for entry in allocation_entries:
        strategy_id = getattr(entry, "strategy_id", None) or entry.get("strategy_id")
        alloc_pct_raw = getattr(entry, "allocation_pct", None) or entry.get("allocation_pct")
        if not strategy_id or alloc_pct_raw is None:
            continue
        try:
            _upsert_allocation_override(
                session=session,
                strategy_id=strategy_id,
                max_pct_of_capital=float(Decimal(str(alloc_pct_raw))),
                actor=actor,
                now=now,
            )
            summary["allocation_overrides_created"] += 1
        except Exception as exc:
            summary["errors"].append(f"allocation[{strategy_id}]: {exc}")

    # 5. Ensure base capital allocation policies exist so PortfolioEngine.get_allocation()
    #    can size positions. Per-strategy overrides set specific percentages; this base
    #    policy is the required fallback that must exist for NoPolicyFoundError not to fire.
    try:
        _ensure_capital_allocation_policies(session=session, now=now)
    except Exception as exc:
        summary["errors"].append(f"capital_allocation_policies: {exc}")

    try:
        session.flush()
    except Exception as exc:
        summary["errors"].append(f"flush: {exc}")
        session.rollback()

    return summary


def _apply_operator_settings(*, session: Session, patch: dict[str, Any], now: datetime) -> None:
    from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
        OperatorSettingsRepository,
    )

    repo = OperatorSettingsRepository(session)
    row = repo.get_or_create_default()

    field_map = {
        "risk_tolerance": ("risk_tolerance", str),
        "max_drawdown_limit": ("max_drawdown_limit", float),
        "auto_promote_enabled": ("auto_promote_enabled", _to_bool),
        "auto_rebalance_enabled": ("auto_rebalance_enabled", _to_bool),
        "rebalance_frequency": ("rebalance_frequency", str),
        "max_strategy_drawdown": ("max_strategy_drawdown", float),
        "per_strategy_cap": ("per_strategy_cap", float),
        "target_portfolio_volatility": ("target_portfolio_volatility", float),
        "min_rebalance_interval_hours": ("min_rebalance_interval_hours", float),
        "min_allocation_change_pct": ("min_allocation_change_pct", float),
        "portfolio_drawdown_action": ("portfolio_drawdown_action", str),
        "portfolio_drawdown_recovery_mode": ("portfolio_drawdown_recovery_mode", str),
        "portfolio_max_drawdown_pct": ("portfolio_max_drawdown_pct", float),
    }

    for fixture_key, (model_field, coerce) in field_map.items():
        if fixture_key in patch:
            setattr(row, model_field, coerce(patch[fixture_key]))  # type: ignore[operator]

    session.add(row)


def _upsert_strategy_governance(
    *,
    session: Session,
    strategy_id: str,
    state: str,
    actor: str,
    now: datetime,
) -> None:
    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )

    config_hash = hashlib.sha256(strategy_id.encode()).hexdigest()[:16]

    existing = (
        session.query(StrategyGovernance)
        .filter(StrategyGovernance.strategy_id == strategy_id)
        .first()
    )
    if existing is not None:
        # Update state only if not already in a more advanced state.
        existing.current_state = state
        existing.updated_at = now
        return

    row = StrategyGovernance(
        strategy_id=strategy_id,
        config_hash=config_hash,
        current_state=state,
        experiment_id="initial_state_seeding",
        source_run_id=None,
        submitted_at=now,
        updated_at=now,
        submitted_by=actor,
    )
    session.add(row)


def _upsert_allocation_override(
    *,
    session: Session,
    strategy_id: str,
    max_pct_of_capital: float,
    actor: str,
    now: datetime,
) -> None:
    from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
        AllocationOverrides,
    )

    # Deactivate any existing active override for this strategy.
    existing = (
        session.query(AllocationOverrides)
        .filter(
            AllocationOverrides.strategy_id == strategy_id,
            AllocationOverrides.is_active.is_(True),
        )
        .first()
    )
    if existing is not None:
        existing.is_active = False

    row = AllocationOverrides(
        override_id=str(uuid4()),
        strategy_id=strategy_id,
        overridden_by=actor,
        override_reason="initial state seeding from fixture",
        max_pct_of_capital=max_pct_of_capital,
        max_position_size_usd=None,
        max_drawdown_allowed=None,
        is_active=True,
        created_at=now,
        expires_at=None,
    )
    session.add(row)


def _ensure_capital_allocation_policies(
    *,
    session: Session,
    now: datetime,
) -> None:
    """Seed default base capital allocation policies if none exist.

    PortfolioEngine.get_allocation() requires an active base policy (performance_tier=None)
    for the strategy's approval_status. Without it, NoPolicyFoundError fires and all order
    intents are skipped. Per-strategy AllocationOverrides set the specific percentages;
    this base policy is just the required fallback.
    """
    from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
        CapitalAllocationPolicies,
    )
    from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
        CapitalAllocationPoliciesRepository,
    )

    repo = CapitalAllocationPoliciesRepository(session)

    # Seed for all allocatable states. Use short-form enum values (what the DB stores
    # in capital_allocation_policies.approval_status).
    for status in ("approved_paper", "approved_live", "approved_research"):
        if repo.get_active_policy(approval_status=status, performance_tier=None) is not None:
            continue
        repo.insert(
            CapitalAllocationPolicies(
                policy_id=str(uuid4()),
                approval_status=status,
                performance_tier=None,
                max_pct_of_capital=0.5,
                max_position_size_usd=None,
                max_drawdown_allowed=0.25,
                is_active=True,
                created_at=now,
                notes="default policy seeded by platform backtest initial state",
            )
        )


def _upsert_strategy_config(
    *,
    session: Session,
    strategy_id: str,
    now: datetime,
) -> None:
    """Ensure a strategy_configs row exists for a seed strategy.

    Maps well-known strategy_id patterns to their registry type. Skips if a row
    already exists so research-generated configs are never overwritten.
    """
    from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs

    if session.get(StrategyConfigs, strategy_id) is not None:
        return

    _SEED_TYPE_MAP: dict[str, str] = {
        "momentum": "momentum",
        "mean_reversion": "mean_reversion",
        "macd_crossover": "moving_average_crossover",
        "moving_average_crossover": "moving_average_crossover",
        "factor_based": "factor_based",
        "composite_rule": "composite_rule",
    }
    strategy_type: str | None = None
    for fragment, stype in _SEED_TYPE_MAP.items():
        if fragment in strategy_id:
            strategy_type = stype
            break
    if strategy_type is None:
        strategy_type = "stub"

    config_hash = hashlib.sha256(strategy_id.encode()).hexdigest()[:16]
    row = StrategyConfigs(
        strategy_id=strategy_id,
        strategy_type=strategy_type,
        config_hash=config_hash,
        config_json={},
        created_at=now,
        metadata_json={"source": "initial_state_seeding"},
    )
    session.add(row)


def _reset_strategy_checkpoint(*, session: Session, strategy_id: str) -> None:
    from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
        StrategyRuntimeState,
    )

    row = session.get(StrategyRuntimeState, strategy_id)
    if row is not None:
        row.last_evaluated_bar_timestamp = None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)
