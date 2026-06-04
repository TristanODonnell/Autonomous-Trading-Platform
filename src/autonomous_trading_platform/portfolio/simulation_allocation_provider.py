# autonomous_trading_platform/portfolio/simulation_allocation_provider.py

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, model_validator

from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.models import AllocationResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structural protocols for the repository parameters of snapshot_allocation_config.
# Using structural protocols (rather than importing concrete repo classes) lets
# tests pass lightweight fakes without a type error, while still constraining
# callers to objects that expose the required methods.
# ---------------------------------------------------------------------------


@runtime_checkable
class _PoliciesRepo(Protocol):
    def get_active_policy(
        self, approval_status: str, performance_tier: str | None = None
    ) -> Any: ...


@runtime_checkable
class _OverridesRepo(Protocol):
    def get_active_override(self, strategy_id: str, now: datetime) -> Any: ...


_ALLOCATABLE_STATUSES = (
    GovernanceState.APPROVED_RESEARCH.value,
    GovernanceState.APPROVED_PAPER.value,
    GovernanceState.APPROVED_LIVE.value,
)


class MissingAllocationConfigError(Exception):
    """Raised when SimulationAllocationProvider receives an invalid AllocationConfig."""


class StrategyAllocationEntry(BaseModel):
    """Allocation parameters for one strategy captured at snapshot time."""

    strategy_id: str
    max_pct_of_capital: float
    max_position_size_usd: float | None = None
    max_drawdown_allowed: float
    policy_id: str
    override_applied: bool = False
    override_id: str | None = None


class AllocationConfig(BaseModel):
    """
    Point-in-time snapshot of allocation parameters for deterministic simulation.

    Captures all allocation inputs needed to reproduce backtest behavior regardless of
    subsequent changes to production CapitalAllocationPolicies or AllocationOverrides.

    ``allocation_config_hash`` is computed deterministically from behavior-affecting
    fields (policy params, defaults, schema_version).  It is stable for semantically
    identical configs, excludes metadata fields (``snapshot_effective_at``,
    ``captured_by``), and is included in SimulationArtifactIdentity and simulation
    manifests for lineage tracking and promotion auditability.

    To reproduce a previous simulation:
      1. Persist with ``to_artifact_dict()``.
      2. Reload with ``AllocationConfig.from_artifact_dict(data)``.
      3. Construct ``SimulationAllocationProvider`` with the reloaded config.
    """

    strategy_entries: dict[str, StrategyAllocationEntry] = {}
    default_max_pct_of_capital: float = 0.10
    default_max_drawdown_allowed: float = 0.20
    default_policy_id: str = "simulation_default"
    total_capital: float
    snapshot_effective_at: datetime
    captured_by: str = "live_db_snapshot"
    schema_version: str = "1.0"
    # Stable hash of behavior fields — excludes metadata so re-snapshotting at a
    # different timestamp yields the same hash if policy params are unchanged.
    allocation_config_hash: str = ""

    @model_validator(mode="after")
    def _ensure_hash(self) -> AllocationConfig:
        if not self.allocation_config_hash:
            object.__setattr__(self, "allocation_config_hash", _compute_allocation_hash(self))
        return self

    def to_artifact_dict(self) -> dict[str, Any]:
        """Serializable dict for persisting with simulation artifacts."""
        return {
            "schema_version": self.schema_version,
            "allocation_config_hash": self.allocation_config_hash,
            "snapshot_effective_at": self.snapshot_effective_at.isoformat(),
            "captured_by": self.captured_by,
            "total_capital": self.total_capital,
            "default_max_pct_of_capital": self.default_max_pct_of_capital,
            "default_max_drawdown_allowed": self.default_max_drawdown_allowed,
            "default_policy_id": self.default_policy_id,
            "strategy_count": len(self.strategy_entries),
            "strategy_entries": {
                sid: entry.model_dump() for sid, entry in sorted(self.strategy_entries.items())
            },
        }

    @classmethod
    def from_artifact_dict(cls, data: dict[str, Any]) -> AllocationConfig:
        """Reload from a persisted artifact dict."""
        strategy_entries = {
            sid: StrategyAllocationEntry(**entry)
            for sid, entry in data.get("strategy_entries", {}).items()
        }
        return cls(
            strategy_entries=strategy_entries,
            default_max_pct_of_capital=data["default_max_pct_of_capital"],
            default_max_drawdown_allowed=data["default_max_drawdown_allowed"],
            default_policy_id=data["default_policy_id"],
            total_capital=data["total_capital"],
            snapshot_effective_at=datetime.fromisoformat(data["snapshot_effective_at"]),
            captured_by=data.get("captured_by", "persisted_artifact"),
            schema_version=data.get("schema_version", "1.0"),
            # Preserve the original hash rather than recomputing — the reloaded config
            # must carry the same hash as when it was first created so that
            # SimulationArtifactIdentity.allocation_config_hash is stable across restores.
            allocation_config_hash=data.get("allocation_config_hash", ""),
        )


def _compute_allocation_hash(config: AllocationConfig) -> str:
    """
    Compute a deterministic 16-char SHA-256 hash over allocation behavior fields.

    Excludes ``snapshot_effective_at`` and ``captured_by`` so that re-snapshotting
    at different timestamps produces the same hash when policy parameters are unchanged.
    """
    entries = sorted(
        [
            {
                "strategy_id": e.strategy_id,
                "max_pct_of_capital": e.max_pct_of_capital,
                "max_position_size_usd": e.max_position_size_usd,
                "max_drawdown_allowed": e.max_drawdown_allowed,
                "policy_id": e.policy_id,
                "override_applied": e.override_applied,
                "override_id": e.override_id,
            }
            for e in config.strategy_entries.values()
        ],
        key=lambda x: str(x["strategy_id"]),
    )
    payload = {
        "schema_version": config.schema_version,
        "default_max_pct_of_capital": config.default_max_pct_of_capital,
        "default_max_drawdown_allowed": config.default_max_drawdown_allowed,
        "default_policy_id": config.default_policy_id,
        "strategy_entries": entries,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class SimulationAllocationProvider:
    """
    Allocation provider for backtest/simulation execution.

    Holds a static ``AllocationConfig`` snapshot captured once at simulation start.
    Never queries ``CapitalAllocationPoliciesRepository`` or
    ``AllocationOverridesRepository`` during simulation runtime — allocation behavior
    is fully deterministic from the pinned config.

    Satisfies the ``IAllocationProvider`` protocol so it can replace ``PortfolioEngine``
    wherever the simulation/backtest execution path accepts an allocation provider.

    Capital updates (``update_total_capital``) are allowed because broker equity
    changes with fills during a simulation run.  The policy snapshot (percentages,
    limits, policy IDs) remains frozen throughout.
    """

    def __init__(self, config: AllocationConfig) -> None:
        if not config.allocation_config_hash:
            raise MissingAllocationConfigError(
                "AllocationConfig must have a non-empty allocation_config_hash. "
                "Construct AllocationConfig normally — the validator computes the hash."
            )
        self._config = config
        self._total_capital = config.total_capital

    @property
    def allocation_config(self) -> AllocationConfig:
        """The static snapshot this provider was constructed with."""
        return self._config

    @property
    def total_capital(self) -> float:
        return self._total_capital

    def update_total_capital(self, new_total: float) -> None:
        """
        Update live capital as equity changes during simulation.

        The policy snapshot (percentages, limits, policy IDs) remains pinned from
        the config — only the dollar amount used to compute ``allocated_capital_usd``
        changes.
        """
        if new_total < 0:
            raise ValueError(f"total_capital cannot be negative, got {new_total}")
        self._total_capital = new_total

    def get_allocation(
        self,
        strategy_id: str,
        approval_status: GovernanceState | None,
        performance_tier: str | None = None,
    ) -> AllocationResult:
        """
        Resolve allocation from the static config — no DB access.

        Uses the strategy-specific entry if captured at snapshot time, otherwise
        falls back to config defaults.  Simulation does not enforce the production
        governance approval gate; any strategy_id (including StubStrategy) may
        allocate.
        """
        entry = self._config.strategy_entries.get(strategy_id)
        if entry is not None:
            max_pct = entry.max_pct_of_capital
            max_position_size_usd = entry.max_position_size_usd
            max_drawdown = entry.max_drawdown_allowed
            policy_id = entry.policy_id
            override_applied = entry.override_applied
            override_id = entry.override_id
        else:
            max_pct = self._config.default_max_pct_of_capital
            max_position_size_usd = None
            max_drawdown = self._config.default_max_drawdown_allowed
            policy_id = self._config.default_policy_id
            override_applied = False
            override_id = None
            logger.debug(
                "simulation_allocation_provider.using_defaults",
                extra={
                    "strategy_id": strategy_id,
                    "default_max_pct_of_capital": max_pct,
                    "allocation_config_hash": self._config.allocation_config_hash,
                },
            )

        resolved_status = (
            approval_status if approval_status is not None else GovernanceState.APPROVED_RESEARCH
        )
        allocated_usd = max_pct * self._total_capital

        return AllocationResult(
            strategy_id=strategy_id,
            approval_status=resolved_status,
            max_pct_of_capital=max_pct,
            max_position_size_usd=max_position_size_usd,
            max_drawdown_allowed=max_drawdown,
            allocated_capital_usd=allocated_usd,
            override_applied=override_applied,
            override_id=override_id,
            policy_id=policy_id,
            capital_source="simulation_config",
        )


def snapshot_allocation_config(
    *,
    policies_repo: _PoliciesRepo,
    overrides_repo: _OverridesRepo,
    total_capital: float,
    strategy_ids: list[str] | None = None,
    captured_by: str = "live_db_snapshot",
) -> AllocationConfig:
    """
    Read current allocation policies and overrides once and serialize to AllocationConfig.

    Call this ONCE at simulation start — before the bar/tick loop begins.
    Pass the resulting ``AllocationConfig`` to ``SimulationAllocationProvider``.
    Do NOT call this function during simulation execution.

    The function reads the APPROVED_RESEARCH base policy to set the config defaults,
    ensuring simulations without explicit strategy entries still receive realistic
    allocation fractions rather than hardcoded constants.

    Args:
        policies_repo: Active DB session-backed policies repository.
        overrides_repo: Active DB session-backed overrides repository.
        total_capital: Initial simulation capital (broker equity at snapshot time).
        strategy_ids: Optional strategy IDs to capture strategy-specific overrides for.
                      If ``None``, no per-strategy entries are recorded.
        captured_by: Source label for the snapshot metadata (not included in hash).
    """
    now = datetime.now(tz=UTC)

    # Read base APPROVED_RESEARCH policy to use as the config defaults.
    # Falls back to hardcoded constants when no policy exists (e.g. test environments).
    base_policy = policies_repo.get_active_policy(
        approval_status=GovernanceState.APPROVED_RESEARCH.value
    )
    default_max_pct = base_policy.max_pct_of_capital if base_policy is not None else 0.10
    default_max_drawdown = base_policy.max_drawdown_allowed if base_policy is not None else 0.20
    default_policy_id = base_policy.policy_id if base_policy is not None else "simulation_default"

    strategy_entries: dict[str, StrategyAllocationEntry] = {}

    if strategy_ids:
        for sid in strategy_ids:
            override = overrides_repo.get_active_override(strategy_id=sid, now=now)
            captured = False
            for status_val in _ALLOCATABLE_STATUSES:
                policy = policies_repo.get_active_policy(approval_status=status_val)
                if policy is None:
                    continue
                max_pct = (
                    override.max_pct_of_capital
                    if override is not None and override.max_pct_of_capital is not None
                    else policy.max_pct_of_capital
                )
                max_position_size_usd = (
                    override.max_position_size_usd
                    if override is not None and override.max_position_size_usd is not None
                    else policy.max_position_size_usd
                )
                max_drawdown = (
                    override.max_drawdown_allowed
                    if override is not None and override.max_drawdown_allowed is not None
                    else policy.max_drawdown_allowed
                )
                strategy_entries[sid] = StrategyAllocationEntry(
                    strategy_id=sid,
                    max_pct_of_capital=max_pct,
                    max_position_size_usd=max_position_size_usd,
                    max_drawdown_allowed=max_drawdown,
                    policy_id=policy.policy_id,
                    override_applied=override is not None,
                    override_id=override.override_id if override is not None else None,
                )
                captured = True
                break
            if not captured:
                strategy_entries[sid] = StrategyAllocationEntry(
                    strategy_id=sid,
                    max_pct_of_capital=(
                        override.max_pct_of_capital
                        if override is not None and override.max_pct_of_capital is not None
                        else default_max_pct
                    ),
                    max_position_size_usd=(
                        override.max_position_size_usd
                        if override is not None and override.max_position_size_usd is not None
                        else None
                    ),
                    max_drawdown_allowed=(
                        override.max_drawdown_allowed
                        if override is not None and override.max_drawdown_allowed is not None
                        else default_max_drawdown
                    ),
                    policy_id=default_policy_id,
                    override_applied=override is not None,
                    override_id=override.override_id if override is not None else None,
                )

    logger.info(
        "simulation_allocation_provider.snapshot_captured",
        extra={
            "strategy_count": len(strategy_entries),
            "strategy_ids": sorted(strategy_entries.keys()),
            "total_capital": total_capital,
            "default_max_pct_of_capital": default_max_pct,
            "captured_by": captured_by,
        },
    )

    return AllocationConfig(
        strategy_entries=strategy_entries,
        default_max_pct_of_capital=default_max_pct,
        default_max_drawdown_allowed=default_max_drawdown,
        default_policy_id=default_policy_id,
        total_capital=total_capital,
        snapshot_effective_at=now,
        captured_by=captured_by,
    )
