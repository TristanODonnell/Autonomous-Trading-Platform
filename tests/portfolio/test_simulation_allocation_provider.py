# tests/portfolio/test_simulation_allocation_provider.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.simulation_allocation_provider import (
    AllocationConfig,
    SimulationAllocationProvider,
    StrategyAllocationEntry,
    snapshot_allocation_config,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
_CAPITAL = 100_000.0


def _entry(
    strategy_id: str = "s1",
    max_pct: float = 0.15,
    max_drawdown: float = 0.20,
    policy_id: str = "pol_001",
    max_position_size_usd: float | None = None,
    override_applied: bool = False,
    override_id: str | None = None,
) -> StrategyAllocationEntry:
    return StrategyAllocationEntry(
        strategy_id=strategy_id,
        max_pct_of_capital=max_pct,
        max_position_size_usd=max_position_size_usd,
        max_drawdown_allowed=max_drawdown,
        policy_id=policy_id,
        override_applied=override_applied,
        override_id=override_id,
    )


def _config(
    strategy_entries: dict[str, StrategyAllocationEntry] | None = None,
    default_max_pct: float = 0.10,
    default_max_drawdown: float = 0.20,
    default_policy_id: str = "simulation_default",
    total_capital: float = _CAPITAL,
    snapshot_effective_at: datetime = _NOW,
) -> AllocationConfig:
    return AllocationConfig(
        strategy_entries=strategy_entries or {},
        default_max_pct_of_capital=default_max_pct,
        default_max_drawdown_allowed=default_max_drawdown,
        default_policy_id=default_policy_id,
        total_capital=total_capital,
        snapshot_effective_at=snapshot_effective_at,
    )


# ---------------------------------------------------------------------------
# Fake repositories — no DB required
# ---------------------------------------------------------------------------


@dataclass
class _FakePolicy:
    policy_id: str
    approval_status: str
    max_pct_of_capital: float
    max_position_size_usd: float | None
    max_drawdown_allowed: float
    is_active: bool = True
    performance_tier: str | None = None


@dataclass
class _FakeOverride:
    override_id: str
    strategy_id: str
    max_pct_of_capital: float | None
    max_position_size_usd: float | None
    max_drawdown_allowed: float | None


class _FakePoliciesRepo:
    def __init__(self, policies: list[_FakePolicy] | None = None) -> None:
        self._policies = policies or []
        self.call_count = 0

    def get_active_policy(
        self, approval_status: str, performance_tier: str | None = None
    ) -> _FakePolicy | None:
        self.call_count += 1
        for p in self._policies:
            if p.approval_status == approval_status and p.is_active:
                return p
        return None


class _FakeOverridesRepo:
    def __init__(self, overrides: list[_FakeOverride] | None = None) -> None:
        self._overrides = overrides or []
        self.call_count = 0

    def get_active_override(self, strategy_id: str, now: datetime) -> _FakeOverride | None:
        self.call_count += 1
        for o in self._overrides:
            if o.strategy_id == strategy_id:
                return o
        return None


# ---------------------------------------------------------------------------
# AllocationConfig — hash behaviour
# ---------------------------------------------------------------------------


class TestAllocationConfigHash:
    def test_hash_is_computed_on_construction(self) -> None:
        cfg = _config()
        assert cfg.allocation_config_hash
        assert len(cfg.allocation_config_hash) == 16

    def test_same_config_produces_same_hash(self) -> None:
        cfg_a = _config(total_capital=100_000.0, snapshot_effective_at=_NOW)
        cfg_b = _config(total_capital=100_000.0, snapshot_effective_at=_NOW)
        assert cfg_a.allocation_config_hash == cfg_b.allocation_config_hash

    def test_hash_is_stable_across_different_snapshot_timestamps(self) -> None:
        """snapshot_effective_at is metadata — must not affect hash."""
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 6, 1, tzinfo=UTC)
        cfg_a = _config(snapshot_effective_at=t1)
        cfg_b = _config(snapshot_effective_at=t2)
        assert cfg_a.allocation_config_hash == cfg_b.allocation_config_hash

    def test_different_default_pct_produces_different_hash(self) -> None:
        cfg_a = _config(default_max_pct=0.10)
        cfg_b = _config(default_max_pct=0.20)
        assert cfg_a.allocation_config_hash != cfg_b.allocation_config_hash

    def test_different_strategy_entries_produce_different_hash(self) -> None:
        cfg_a = _config(strategy_entries={"s1": _entry("s1", max_pct=0.10)})
        cfg_b = _config(strategy_entries={"s1": _entry("s1", max_pct=0.25)})
        assert cfg_a.allocation_config_hash != cfg_b.allocation_config_hash

    def test_hash_is_stable_regardless_of_dict_insertion_order(self) -> None:
        """Strategy entry ordering must not affect hash."""
        entries_ab = {"s_a": _entry("s_a"), "s_b": _entry("s_b", max_pct=0.20)}
        entries_ba = {"s_b": _entry("s_b", max_pct=0.20), "s_a": _entry("s_a")}
        cfg_ab = AllocationConfig(
            strategy_entries=entries_ab,
            total_capital=_CAPITAL,
            snapshot_effective_at=_NOW,
        )
        cfg_ba = AllocationConfig(
            strategy_entries=entries_ba,
            total_capital=_CAPITAL,
            snapshot_effective_at=_NOW,
        )
        assert cfg_ab.allocation_config_hash == cfg_ba.allocation_config_hash

    def test_adding_a_strategy_entry_changes_hash(self) -> None:
        cfg_empty = _config()
        cfg_with = _config(strategy_entries={"s1": _entry("s1")})
        assert cfg_empty.allocation_config_hash != cfg_with.allocation_config_hash


# ---------------------------------------------------------------------------
# AllocationConfig — serialisation roundtrip
# ---------------------------------------------------------------------------


class TestAllocationConfigSerialization:
    def test_to_artifact_dict_roundtrip(self) -> None:
        original = AllocationConfig(
            strategy_entries={
                "momentum_v2": _entry("momentum_v2", max_pct=0.15, policy_id="pol_live"),
            },
            default_max_pct_of_capital=0.10,
            default_max_drawdown_allowed=0.20,
            default_policy_id="sim_default",
            total_capital=200_000.0,
            snapshot_effective_at=_NOW,
            captured_by="live_db_snapshot",
        )

        artifact = original.to_artifact_dict()
        restored = AllocationConfig.from_artifact_dict(artifact)

        assert restored.allocation_config_hash == original.allocation_config_hash
        assert restored.default_max_pct_of_capital == original.default_max_pct_of_capital
        assert restored.total_capital == original.total_capital
        assert "momentum_v2" in restored.strategy_entries
        assert restored.strategy_entries["momentum_v2"].max_pct_of_capital == 0.15

    def test_from_artifact_dict_preserves_original_hash(self) -> None:
        """Restored config must carry the same hash, not recompute it."""
        original = _config(strategy_entries={"s1": _entry("s1")})
        artifact = original.to_artifact_dict()

        # Mutate the artifact timestamp — hash should still match original
        artifact["snapshot_effective_at"] = "2099-01-01T00:00:00+00:00"
        restored = AllocationConfig.from_artifact_dict(artifact)
        assert restored.allocation_config_hash == original.allocation_config_hash

    def test_artifact_dict_contains_schema_version(self) -> None:
        cfg = _config()
        d = cfg.to_artifact_dict()
        assert d["schema_version"] == "1.0"

    def test_artifact_dict_contains_strategy_count(self) -> None:
        cfg = _config(strategy_entries={"s1": _entry("s1"), "s2": _entry("s2")})
        d = cfg.to_artifact_dict()
        assert d["strategy_count"] == 2


# ---------------------------------------------------------------------------
# SimulationAllocationProvider — basic allocation
# ---------------------------------------------------------------------------


class TestSimulationAllocationProviderAllocation:
    def test_returns_strategy_specific_entry(self) -> None:
        cfg = _config(strategy_entries={"momentum_v2": _entry("momentum_v2", max_pct=0.15)})
        provider = SimulationAllocationProvider(cfg)

        result = provider.get_allocation("momentum_v2", GovernanceState.APPROVED_RESEARCH)

        assert result.strategy_id == "momentum_v2"
        assert result.max_pct_of_capital == 0.15
        assert result.allocated_capital_usd == pytest.approx(0.15 * _CAPITAL)
        assert result.policy_id == "pol_001"
        assert result.capital_source == "simulation_config"

    def test_falls_back_to_defaults_for_unknown_strategy(self) -> None:
        cfg = _config(default_max_pct=0.08)
        provider = SimulationAllocationProvider(cfg)

        result = provider.get_allocation("unknown_strat", None)

        assert result.strategy_id == "unknown_strat"
        assert result.max_pct_of_capital == 0.08
        assert result.allocated_capital_usd == pytest.approx(0.08 * _CAPITAL)
        assert result.policy_id == "simulation_default"

    def test_no_governance_gate_for_unapproved_status(self) -> None:
        """Simulation provider must not raise AllocationDeniedError."""
        cfg = _config()
        provider = SimulationAllocationProvider(cfg)

        # PROPOSED would be blocked by PortfolioEngine — should succeed here
        result = provider.get_allocation("strat_x", GovernanceState.PROPOSED)
        assert result.strategy_id == "strat_x"

    def test_none_approval_status_resolves_to_approved_research(self) -> None:
        cfg = _config()
        provider = SimulationAllocationProvider(cfg)
        result = provider.get_allocation("strat_x", None)
        assert result.approval_status == GovernanceState.APPROVED_RESEARCH

    def test_override_fields_propagated_from_entry(self) -> None:
        entry = _entry("s1", override_applied=True, override_id="ov_abc")
        cfg = _config(strategy_entries={"s1": entry})
        provider = SimulationAllocationProvider(cfg)

        result = provider.get_allocation("s1", GovernanceState.APPROVED_LIVE)
        assert result.override_applied is True
        assert result.override_id == "ov_abc"

    def test_max_position_size_usd_propagated(self) -> None:
        entry = _entry("s1", max_position_size_usd=5000.0)
        cfg = _config(strategy_entries={"s1": entry})
        provider = SimulationAllocationProvider(cfg)

        result = provider.get_allocation("s1", GovernanceState.APPROVED_RESEARCH)
        assert result.max_position_size_usd == 5000.0


# ---------------------------------------------------------------------------
# SimulationAllocationProvider — capital management
# ---------------------------------------------------------------------------


class TestSimulationAllocationProviderCapital:
    def test_total_capital_matches_config_at_construction(self) -> None:
        cfg = _config(total_capital=250_000.0)
        provider = SimulationAllocationProvider(cfg)
        assert provider.total_capital == 250_000.0

    def test_update_total_capital_changes_allocated_usd(self) -> None:
        cfg = _config(default_max_pct=0.10, total_capital=100_000.0)
        provider = SimulationAllocationProvider(cfg)

        provider.update_total_capital(200_000.0)

        result = provider.get_allocation("any", None)
        assert result.allocated_capital_usd == pytest.approx(0.10 * 200_000.0)

    def test_update_total_capital_does_not_change_policy_hash(self) -> None:
        """Updating capital must not mutate the pinned AllocationConfig."""
        cfg = _config(total_capital=100_000.0)
        original_hash = cfg.allocation_config_hash
        provider = SimulationAllocationProvider(cfg)

        provider.update_total_capital(999_999.0)

        assert provider.allocation_config.allocation_config_hash == original_hash

    def test_update_total_capital_negative_raises(self) -> None:
        cfg = _config()
        provider = SimulationAllocationProvider(cfg)
        with pytest.raises(ValueError, match="negative"):
            provider.update_total_capital(-1.0)

    def test_policy_snapshot_is_frozen_when_capital_changes(self) -> None:
        """Config default_max_pct must not change regardless of capital updates."""
        cfg = _config(default_max_pct=0.12)
        provider = SimulationAllocationProvider(cfg)
        provider.update_total_capital(500_000.0)
        result = provider.get_allocation("strat", None)
        assert result.max_pct_of_capital == 0.12


# ---------------------------------------------------------------------------
# Reproducibility — production policy change must not affect in-flight simulation
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_same_config_produces_same_allocation_decisions(self) -> None:
        entries = {"s1": _entry("s1", max_pct=0.20)}
        cfg_a = AllocationConfig(
            strategy_entries=entries,
            total_capital=100_000.0,
            snapshot_effective_at=_NOW,
        )
        cfg_b = AllocationConfig(
            strategy_entries=entries,
            total_capital=100_000.0,
            snapshot_effective_at=_NOW,
        )
        assert cfg_a.allocation_config_hash == cfg_b.allocation_config_hash

        p_a = SimulationAllocationProvider(cfg_a)
        p_b = SimulationAllocationProvider(cfg_b)

        r_a = p_a.get_allocation("s1", GovernanceState.APPROVED_RESEARCH)
        r_b = p_b.get_allocation("s1", GovernanceState.APPROVED_RESEARCH)
        assert r_a.allocated_capital_usd == r_b.allocated_capital_usd
        assert r_a.max_pct_of_capital == r_b.max_pct_of_capital

    def test_production_repo_change_after_snapshot_does_not_affect_simulation(self) -> None:
        """
        Core reproducibility guarantee: once the config is snapshotted, mutating
        what the DB would return has zero effect on the provider's decisions.
        """
        policies_repo = _FakePoliciesRepo(
            policies=[
                _FakePolicy(
                    policy_id="pol_001",
                    approval_status="approved_research",
                    max_pct_of_capital=0.10,
                    max_position_size_usd=None,
                    max_drawdown_allowed=0.20,
                )
            ]
        )
        overrides_repo = _FakeOverridesRepo()

        cfg = snapshot_allocation_config(
            policies_repo=policies_repo,
            overrides_repo=overrides_repo,
            total_capital=100_000.0,
        )
        provider = SimulationAllocationProvider(cfg)

        # Simulate production policy change AFTER snapshot
        policies_repo._policies[0].max_pct_of_capital = 0.50

        # Provider must still return the snapshotted value
        result = provider.get_allocation("any_strat", None)
        assert result.max_pct_of_capital == pytest.approx(0.10)
        assert result.allocated_capital_usd == pytest.approx(0.10 * 100_000.0)

    def test_no_db_calls_during_get_allocation(self) -> None:
        """Provider must never query repositories after construction."""
        policies_repo = _FakePoliciesRepo(
            policies=[
                _FakePolicy(
                    policy_id="pol_001",
                    approval_status="approved_research",
                    max_pct_of_capital=0.10,
                    max_position_size_usd=None,
                    max_drawdown_allowed=0.20,
                )
            ]
        )
        overrides_repo = _FakeOverridesRepo()

        cfg = snapshot_allocation_config(
            policies_repo=policies_repo,
            overrides_repo=overrides_repo,
            total_capital=100_000.0,
        )
        provider = SimulationAllocationProvider(cfg)

        calls_before = policies_repo.call_count + overrides_repo.call_count

        # Multiple allocation calls — repos must not be touched
        provider.get_allocation("strat_a", None)
        provider.get_allocation("strat_b", GovernanceState.APPROVED_LIVE)
        provider.get_allocation("strat_c", GovernanceState.APPROVED_PAPER)

        calls_after = policies_repo.call_count + overrides_repo.call_count
        assert calls_before == calls_after, (
            f"Repos were queried {calls_after - calls_before} extra times during simulation"
        )

    def test_rerunning_with_persisted_config_reproduces_allocations(self) -> None:
        """Persisted config must restore identical allocation behavior."""
        original_cfg = AllocationConfig(
            strategy_entries={"momentum_v2": _entry("momentum_v2", max_pct=0.18)},
            default_max_pct_of_capital=0.10,
            default_max_drawdown_allowed=0.25,
            default_policy_id="sim_default",
            total_capital=100_000.0,
            snapshot_effective_at=_NOW,
        )
        artifact = original_cfg.to_artifact_dict()
        restored_cfg = AllocationConfig.from_artifact_dict(artifact)

        p_original = SimulationAllocationProvider(original_cfg)
        p_restored = SimulationAllocationProvider(restored_cfg)

        r_orig = p_original.get_allocation("momentum_v2", GovernanceState.APPROVED_RESEARCH)
        r_rest = p_restored.get_allocation("momentum_v2", GovernanceState.APPROVED_RESEARCH)

        assert r_orig.allocated_capital_usd == r_rest.allocated_capital_usd
        assert r_orig.max_pct_of_capital == r_rest.max_pct_of_capital
        assert r_orig.policy_id == r_rest.policy_id


# ---------------------------------------------------------------------------
# snapshot_allocation_config — reads from repos once at simulation start
# ---------------------------------------------------------------------------


class TestSnapshotAllocationConfig:
    def test_returns_valid_allocation_config(self) -> None:
        policies_repo = _FakePoliciesRepo(
            policies=[
                _FakePolicy(
                    policy_id="pol_001",
                    approval_status="approved_research",
                    max_pct_of_capital=0.12,
                    max_position_size_usd=None,
                    max_drawdown_allowed=0.25,
                )
            ]
        )
        overrides_repo = _FakeOverridesRepo()

        cfg = snapshot_allocation_config(
            policies_repo=policies_repo,
            overrides_repo=overrides_repo,
            total_capital=100_000.0,
        )

        assert cfg.total_capital == 100_000.0
        assert cfg.allocation_config_hash
        assert cfg.default_max_pct_of_capital == pytest.approx(0.12)
        assert cfg.default_policy_id == "pol_001"

    def test_defaults_when_no_active_policy_exists(self) -> None:
        cfg = snapshot_allocation_config(
            policies_repo=_FakePoliciesRepo(),
            overrides_repo=_FakeOverridesRepo(),
            total_capital=50_000.0,
        )

        assert cfg.default_max_pct_of_capital == pytest.approx(0.10)
        assert cfg.default_policy_id == "simulation_default"

    def test_captures_default_entry_when_strategy_ids_provided_without_policy(self) -> None:
        cfg = snapshot_allocation_config(
            policies_repo=_FakePoliciesRepo(),
            overrides_repo=_FakeOverridesRepo(),
            total_capital=50_000.0,
            strategy_ids=["momentum_v2"],
        )

        entry = cfg.strategy_entries["momentum_v2"]
        assert entry.max_pct_of_capital == pytest.approx(0.10)
        assert entry.max_drawdown_allowed == pytest.approx(0.20)
        assert entry.policy_id == "simulation_default"
        assert entry.override_applied is False

    def test_captures_per_strategy_entry_when_strategy_ids_provided(self) -> None:
        policies_repo = _FakePoliciesRepo(
            policies=[
                _FakePolicy(
                    policy_id="pol_research",
                    approval_status="approved_research",
                    max_pct_of_capital=0.15,
                    max_position_size_usd=None,
                    max_drawdown_allowed=0.20,
                )
            ]
        )
        overrides_repo = _FakeOverridesRepo()

        cfg = snapshot_allocation_config(
            policies_repo=policies_repo,
            overrides_repo=overrides_repo,
            total_capital=100_000.0,
            strategy_ids=["momentum_v2"],
        )

        assert "momentum_v2" in cfg.strategy_entries
        assert cfg.strategy_entries["momentum_v2"].max_pct_of_capital == pytest.approx(0.15)
        assert cfg.strategy_entries["momentum_v2"].policy_id == "pol_research"

    def test_override_values_take_precedence_over_policy(self) -> None:
        policies_repo = _FakePoliciesRepo(
            policies=[
                _FakePolicy(
                    policy_id="pol_research",
                    approval_status="approved_research",
                    max_pct_of_capital=0.10,
                    max_position_size_usd=None,
                    max_drawdown_allowed=0.20,
                )
            ]
        )
        overrides_repo = _FakeOverridesRepo(
            overrides=[
                _FakeOverride(
                    override_id="ov_123",
                    strategy_id="momentum_v2",
                    max_pct_of_capital=0.30,
                    max_position_size_usd=8000.0,
                    max_drawdown_allowed=None,
                )
            ]
        )

        cfg = snapshot_allocation_config(
            policies_repo=policies_repo,
            overrides_repo=overrides_repo,
            total_capital=100_000.0,
            strategy_ids=["momentum_v2"],
        )

        entry = cfg.strategy_entries["momentum_v2"]
        assert entry.max_pct_of_capital == pytest.approx(0.30)
        assert entry.max_position_size_usd == pytest.approx(8000.0)
        assert entry.max_drawdown_allowed == pytest.approx(0.20)  # from policy (override is None)
        assert entry.override_applied is True
        assert entry.override_id == "ov_123"

    def test_override_values_are_captured_when_policy_is_missing(self) -> None:
        overrides_repo = _FakeOverridesRepo(
            overrides=[
                _FakeOverride(
                    override_id="ov_123",
                    strategy_id="momentum_v2",
                    max_pct_of_capital=0.30,
                    max_position_size_usd=8000.0,
                    max_drawdown_allowed=None,
                )
            ]
        )

        cfg = snapshot_allocation_config(
            policies_repo=_FakePoliciesRepo(),
            overrides_repo=overrides_repo,
            total_capital=100_000.0,
            strategy_ids=["momentum_v2"],
        )

        entry = cfg.strategy_entries["momentum_v2"]
        assert entry.max_pct_of_capital == pytest.approx(0.30)
        assert entry.max_position_size_usd == pytest.approx(8000.0)
        assert entry.max_drawdown_allowed == pytest.approx(0.20)
        assert entry.policy_id == "simulation_default"
        assert entry.override_applied is True
        assert entry.override_id == "ov_123"

    def test_no_strategy_entries_when_strategy_ids_is_none(self) -> None:
        policies_repo = _FakePoliciesRepo(
            policies=[
                _FakePolicy(
                    policy_id="pol_001",
                    approval_status="approved_research",
                    max_pct_of_capital=0.10,
                    max_position_size_usd=None,
                    max_drawdown_allowed=0.20,
                )
            ]
        )
        cfg = snapshot_allocation_config(
            policies_repo=policies_repo,
            overrides_repo=_FakeOverridesRepo(),
            total_capital=100_000.0,
            strategy_ids=None,
        )
        assert cfg.strategy_entries == {}

    def test_captured_by_metadata_is_preserved(self) -> None:
        cfg = snapshot_allocation_config(
            policies_repo=_FakePoliciesRepo(),
            overrides_repo=_FakeOverridesRepo(),
            total_capital=100_000.0,
            captured_by="explicit_config",
        )
        assert cfg.captured_by == "explicit_config"

    def test_snapshot_effective_at_is_recent_utc(self) -> None:
        before = datetime.now(UTC)
        cfg = snapshot_allocation_config(
            policies_repo=_FakePoliciesRepo(),
            overrides_repo=_FakeOverridesRepo(),
            total_capital=100_000.0,
        )
        after = datetime.now(UTC)
        assert before <= cfg.snapshot_effective_at <= after


# ---------------------------------------------------------------------------
# SimulationArtifactIdentity integration — hash propagation
# ---------------------------------------------------------------------------


class TestArtifactIdentityIntegration:
    def test_allocation_config_hash_included_in_artifact_identity(self) -> None:
        from autonomous_trading_platform.research.simulation.artifact_identity import (
            SimulationArtifactIdentity,
        )

        cfg = _config(strategy_entries={"s1": _entry("s1")})
        provider = SimulationAllocationProvider(cfg)

        identity = SimulationArtifactIdentity(
            run_id="run_abc",
            experiment_id="exp_001",
            strategy_id="s1",
            dataset_version="v3",
            stage_name="backtest",
            window_role="test",
            allocation_config_hash=provider.allocation_config.allocation_config_hash,
        )

        manifest = identity.to_manifest_dict()
        assert "allocation_config_hash" in manifest
        assert manifest["allocation_config_hash"] == cfg.allocation_config_hash

    def test_manifest_dict_omits_allocation_config_hash_when_not_set(self) -> None:
        from autonomous_trading_platform.research.simulation.artifact_identity import (
            SimulationArtifactIdentity,
        )

        identity = SimulationArtifactIdentity(
            run_id="run_abc",
            experiment_id="exp_001",
            strategy_id="s1",
            dataset_version="v3",
            stage_name="backtest",
            window_role="test",
        )
        manifest = identity.to_manifest_dict()
        assert "allocation_config_hash" not in manifest


# ---------------------------------------------------------------------------
# IAllocationProvider protocol compliance
# ---------------------------------------------------------------------------


class TestIAllocationProviderCompliance:
    def test_simulation_provider_satisfies_protocol(self) -> None:
        from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider

        cfg = _config()
        provider = SimulationAllocationProvider(cfg)
        assert isinstance(provider, IAllocationProvider)

    def test_portfolio_engine_still_satisfies_protocol(self) -> None:
        """Live path must remain unaffected by the provider boundary change."""
        from unittest.mock import MagicMock

        from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider
        from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine

        mock_policies = MagicMock()
        mock_overrides = MagicMock()
        mock_rules = MagicMock()

        engine = PortfolioEngine(
            policies_repo=mock_policies,
            overrides_repo=mock_overrides,
            promotion_rules_repo=mock_rules,
            total_capital=100_000.0,
        )
        assert isinstance(engine, IAllocationProvider)
