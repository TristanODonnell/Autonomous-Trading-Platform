"""Tests for UniverseRebalanceService and _compute_rebalance."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.universe.services.universe_rebalance_service import (
    RebalanceResult,
    UniverseRebalanceService,
    _compute_rebalance,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig

_NOW = datetime(2025, 6, 1, 16, 0, tzinfo=UTC)
_ACTIVE_ID = "active-version-001"
_CANDIDATE_ID = "candidate-version-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_version(
    version_id: str = _ACTIVE_ID,
    status: str = UniverseStatus.ACTIVE,
    effective_from: datetime = _NOW,
) -> UniverseVersion:
    return UniverseVersion(
        universe_version_id=version_id,
        name=f"test_{status}",
        source=UniverseSource.CUSTOM,
        created_at=_NOW,
        effective_from=effective_from,
        effective_to=None,
        status=status,
        rebalance_reason=None,
        config_hash="testhash",
        generation_metadata_json=None,
    )


def _make_member(
    version_id: str = _ACTIVE_ID,
    symbol: str = "AAPL",
    rank: int | None = None,
    score: float | None = None,
    included_reason: str | None = "ranked_1",
    excluded_reason: str | None = None,
) -> UniverseMember:
    return UniverseMember(
        universe_version_id=version_id,
        symbol=symbol,
        rank=rank,
        score=score,
        included_reason=included_reason,
        excluded_reason=excluded_reason,
        liquidity_metrics_json=None,
        quality_metrics_json=None,
    )


def _active_members(*symbols: str) -> list[UniverseMember]:
    return [_make_member(version_id=_ACTIVE_ID, symbol=s, rank=None) for s in symbols]


def _candidate_members(*entries: tuple[str, int | None, str | None]) -> list[UniverseMember]:
    """entries: (symbol, rank, excluded_reason)"""
    result = []
    for symbol, rank, excluded_reason in entries:
        result.append(
            _make_member(
                version_id=_CANDIDATE_ID,
                symbol=symbol,
                rank=rank,
                excluded_reason=excluded_reason,
                included_reason="ranked" if excluded_reason is None else None,
            )
        )
    return result


def _default_config(**overrides) -> UniverseRebalanceConfig:
    return UniverseRebalanceConfig(
        target_universe_size=overrides.pop("target_universe_size", 500),
        max_churn_pct=overrides.pop("max_churn_pct", 0.20),
        retain_until_rank_greater_than=overrides.pop("retain_until_rank_greater_than", 650),
        add_only_if_rank_less_than_or_equal=overrides.pop(
            "add_only_if_rank_less_than_or_equal", 400
        ),
        force_rebalance=overrides.pop("force_rebalance", False),
        allow_empty_active_universe_bootstrap=overrides.pop(
            "allow_empty_active_universe_bootstrap", True
        ),
        **overrides,
    )


def _run(
    *,
    active_members: list[UniverseMember],
    candidate_members: list[UniverseMember],
    config: UniverseRebalanceConfig | None = None,
    active_version: UniverseVersion | None = None,
    candidate_version: UniverseVersion | None = None,
    name: str | None = None,
) -> RebalanceResult:
    if config is None:
        config = _default_config()
    if active_version is None and active_members:
        active_version = _make_version(_ACTIVE_ID, UniverseStatus.ACTIVE)
    if candidate_version is None:
        candidate_version = _make_version(_CANDIDATE_ID, UniverseStatus.CANDIDATE)
    return _compute_rebalance(
        active_version=active_version,
        active_members=active_members,
        candidate_version=candidate_version,
        candidate_members=candidate_members,
        config=config,
        name=name,
    )


# ---------------------------------------------------------------------------
# Bootstrap tests
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_bootstrap_produces_proposed_version_from_candidate(self):
        result = _run(
            active_version=None,
            active_members=[],
            candidate_members=_candidate_members(
                ("AAPL", 1, None), ("MSFT", 2, None), ("GOOG", 3, None)
            ),
        )
        assert result.proposed_version.status == UniverseStatus.PROPOSED
        assert set(result.added) == {"AAPL", "MSFT", "GOOG"}
        assert result.removed == []
        assert result.retained == []
        assert result.churn_pct == 1.0

    def test_bootstrap_respects_target_universe_size(self):
        members = _candidate_members(*[(f"SYM{i:03d}", i, None) for i in range(1, 11)])
        config = _default_config(target_universe_size=5)
        result = _run(
            active_version=None, active_members=[], candidate_members=members, config=config
        )
        assert len(result.added) == 5
        assert len(result.proposed_members) == 5
        # Should take the top 5 by rank
        assert result.added == ["SYM001", "SYM002", "SYM003", "SYM004", "SYM005"]

    def test_bootstrap_not_allowed_raises(self):
        config = _default_config(allow_empty_active_universe_bootstrap=False)
        with pytest.raises(ValueError, match="allow_empty_active_universe_bootstrap"):
            _run(active_version=None, active_members=[], candidate_members=[], config=config)

    def test_bootstrap_proposed_members_have_bootstrap_reason(self):
        result = _run(
            active_version=None,
            active_members=[],
            candidate_members=_candidate_members(("AAPL", 1, None)),
        )
        included = [m for m in result.proposed_members if m.excluded_reason is None]
        assert all(m.included_reason == "bootstrap" for m in included)

    def test_bootstrap_rebalance_run_has_null_previous_version(self):
        result = _run(
            active_version=None,
            active_members=[],
            candidate_members=_candidate_members(("AAPL", 1, None)),
        )
        assert result.rebalance_run.previous_universe_version_id is None
        assert result.rebalance_run.status == "accepted"

    def test_bootstrap_generation_metadata_type_is_bootstrap(self):
        result = _run(
            active_version=None,
            active_members=[],
            candidate_members=_candidate_members(("AAPL", 1, None)),
        )
        assert result.proposed_version.generation_metadata_json["rebalance_type"] == "bootstrap"


# ---------------------------------------------------------------------------
# No-change tests
# ---------------------------------------------------------------------------


class TestNoChange:
    def test_no_change_when_candidate_matches_active_exactly(self):
        members = [("AAPL", 50, None), ("MSFT", 100, None), ("GOOG", 150, None)]
        result = _run(
            active_members=_active_members("AAPL", "MSFT", "GOOG"),
            candidate_members=_candidate_members(*members),
        )
        assert result.added == []
        assert result.removed == []
        assert set(result.retained) == {"AAPL", "MSFT", "GOOG"}
        assert result.churn_pct == 0.0

    def test_no_change_proposed_version_status_is_proposed(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 1, None)),
        )
        assert result.proposed_version.status == UniverseStatus.PROPOSED


# ---------------------------------------------------------------------------
# Normal additions
# ---------------------------------------------------------------------------


class TestNormalAdditions:
    def test_new_high_ranked_symbol_added(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("MSFT", 100, None)),
            config=_default_config(add_only_if_rank_less_than_or_equal=400, max_churn_pct=1.0),
        )
        assert "MSFT" in result.added

    def test_symbol_above_add_threshold_not_added(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("TSLA", 450, None)),
            config=_default_config(add_only_if_rank_less_than_or_equal=400, max_churn_pct=1.0),
        )
        assert "TSLA" not in result.added
        assert "TSLA" not in result.retained

    def test_add_threshold_boundary_included(self):
        """Symbol at exactly the add threshold should be added."""
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("NVDA", 400, None)),
            config=_default_config(add_only_if_rank_less_than_or_equal=400, max_churn_pct=1.0),
        )
        assert "NVDA" in result.added

    def test_add_threshold_boundary_excluded(self):
        """Symbol just above add threshold should not be added."""
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("NVDA", 401, None)),
            config=_default_config(add_only_if_rank_less_than_or_equal=400, max_churn_pct=1.0),
        )
        assert "NVDA" not in result.added

    def test_added_members_have_correct_included_reason(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("MSFT", 100, None)),
            config=_default_config(max_churn_pct=1.0),
        )
        added_member = next(
            m for m in result.proposed_members if m.symbol == "MSFT" and m.excluded_reason is None
        )
        assert added_member.included_reason == "added_from_candidate"


# ---------------------------------------------------------------------------
# Normal removals
# ---------------------------------------------------------------------------


class TestNormalRemovals:
    def test_symbol_excluded_from_candidate_is_removed(self):
        result = _run(
            active_members=_active_members("AAPL", "DELIST"),
            candidate_members=_candidate_members(
                ("AAPL", 50, None), ("DELIST", None, "price_below_minimum")
            ),
        )
        assert "DELIST" in result.removed
        assert "DELIST" not in result.retained

    def test_symbol_not_in_candidate_is_removed(self):
        result = _run(
            active_members=_active_members("AAPL", "GONE"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        assert "GONE" in result.removed

    def test_removed_members_are_stored_as_excluded_in_proposed(self):
        result = _run(
            active_members=_active_members("AAPL", "GONE"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        excluded = [m for m in result.proposed_members if m.symbol == "GONE"]
        assert len(excluded) == 1
        assert excluded[0].excluded_reason is not None
        assert "removed" in excluded[0].excluded_reason

    def test_excluded_candidate_members_not_added(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(
                ("AAPL", 50, None), ("BADSTOCK", None, "insufficient_liquidity")
            ),
        )
        assert "BADSTOCK" not in result.added


# ---------------------------------------------------------------------------
# Hysteresis retention tests
# ---------------------------------------------------------------------------


class TestHysteresisRetention:
    def test_symbol_within_retain_threshold_is_retained(self):
        """Rank 600 with retain_until=650 → should be retained."""
        result = _run(
            active_members=_active_members("AAPL", "MEDIOCRE"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("MEDIOCRE", 600, None)),
            config=_default_config(retain_until_rank_greater_than=650),
        )
        assert "MEDIOCRE" in result.retained
        assert "MEDIOCRE" not in result.removed

    def test_symbol_beyond_retain_threshold_is_optionally_removed(self):
        """Rank 700 with retain_until=650 → should be in optional_removes."""
        result = _run(
            active_members=_active_members("AAPL", "DEGRADED"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("DEGRADED", 700, None)),
            config=_default_config(
                retain_until_rank_greater_than=650,
                max_churn_pct=1.0,  # high churn to ensure removal
            ),
        )
        assert "DEGRADED" in result.removed

    def test_retain_threshold_boundary_kept(self):
        """Symbol at exactly retain_until_rank_greater_than is retained."""
        result = _run(
            active_members=_active_members("AAPL", "EDGE"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("EDGE", 650, None)),
            config=_default_config(retain_until_rank_greater_than=650),
        )
        assert "EDGE" in result.retained

    def test_retain_threshold_one_over_is_optional_remove(self):
        """Symbol at retain_until + 1 goes to optional removes."""
        result = _run(
            active_members=_active_members("AAPL", "EDGE"),
            candidate_members=_candidate_members(("AAPL", 50, None), ("EDGE", 651, None)),
            config=_default_config(retain_until_rank_greater_than=650, max_churn_pct=1.0),
        )
        assert "EDGE" in result.removed

    def test_retained_within_threshold_reason(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        member = next(m for m in result.proposed_members if m.symbol == "AAPL")
        assert member.included_reason == "retained_within_threshold"


# ---------------------------------------------------------------------------
# Churn enforcement tests
# ---------------------------------------------------------------------------


class TestChurnEnforcement:
    def test_max_churn_limits_optional_removes(self):
        """4 active symbols, max_churn=0.25 → max 1 change. D is mandatory, so 0 optional."""
        result = _run(
            active_members=_active_members("A", "B", "C", "D"),
            candidate_members=_candidate_members(
                ("A", 50, None),
                ("B", 700, None),  # optional remove (rank > 650)
                ("C", 100, None),
                # D not in candidate → mandatory remove
            ),
            config=_default_config(
                retain_until_rank_greater_than=650,
                max_churn_pct=0.25,
                add_only_if_rank_less_than_or_equal=400,
            ),
        )
        # D is mandatory (uses the 1 change budget), B should be skipped
        assert "D" in result.removed
        assert "B" in result.retained or "B" in result.skipped_removes

    def test_max_churn_limits_adds(self):
        """With tight churn budget, adds should be capped."""
        active = _active_members("AAPL", "MSFT")  # size = 2
        candidate = _candidate_members(
            ("AAPL", 50, None),
            ("MSFT", 100, None),
            ("NEW1", 200, None),
            ("NEW2", 250, None),
            ("NEW3", 300, None),
        )
        config = _default_config(max_churn_pct=0.10)  # max 0 changes for size=2
        result = _run(active_members=active, candidate_members=candidate, config=config)
        # 10% of 2 = 0.2 → int = 0 → no changes allowed
        assert result.added == []
        assert result.removed == []

    def test_skipped_adds_are_tracked(self):
        active = _active_members("AAPL")  # size = 1
        candidate = _candidate_members(
            ("AAPL", 50, None),
            ("NEW1", 100, None),
            ("NEW2", 150, None),
        )
        config = _default_config(max_churn_pct=0.10)  # max 0 changes → no adds
        result = _run(active_members=active, candidate_members=candidate, config=config)
        # Both new symbols should be skipped
        assert len(result.skipped_adds) == 2

    def test_skipped_removes_are_tracked(self):
        active = _active_members("A", "B", "C", "D")  # size = 4
        candidate = _candidate_members(
            ("A", 50, None),
            ("B", 700, None),  # optional remove
            ("C", 750, None),  # optional remove
            ("D", 800, None),  # optional remove
        )
        # max_churn=0.25 → max 1 change total, 0 mandatory → 1 for optional
        config = _default_config(
            retain_until_rank_greater_than=650,
            max_churn_pct=0.25,
            add_only_if_rank_less_than_or_equal=400,
        )
        result = _run(active_members=active, candidate_members=candidate, config=config)
        # 1 optional remove happens (worst rank first = D rank 800), 2 skipped
        assert len(result.skipped_removes) == 2
        # D (worst rank) should be removed, B and C skipped
        assert "D" in result.removed

    def test_churn_pct_reported_correctly(self):
        result = _run(
            active_members=_active_members("AAPL", "MSFT", "GOOG", "AMZN"),
            candidate_members=_candidate_members(
                ("AAPL", 50, None),
                ("MSFT", 100, None),
                # GOOG and AMZN not in candidate → mandatory removes
            ),
            config=_default_config(max_churn_pct=1.0),
        )
        # 2 removes, 0 adds, active_size = 4 → churn = 0.5
        assert result.churn_pct == pytest.approx(0.5, abs=1e-6)

    def test_mandatory_removes_consume_budget(self):
        """Mandatory removes happen regardless; they reduce remaining budget."""
        active = _active_members("A", "B", "C", "D")  # size = 4
        candidate = _candidate_members(
            ("A", 50, None),
            ("B", 700, None),  # optional remove
            ("C", 750, None),  # optional remove
            # D missing → mandatory remove (consumes 1 of 1 budget)
        )
        config = _default_config(
            retain_until_rank_greater_than=650,
            max_churn_pct=0.25,  # max 1 change
        )
        result = _run(active_members=active, candidate_members=candidate, config=config)
        assert "D" in result.removed
        # B and C should be skipped (budget exhausted by D's mandatory remove)
        assert len(result.skipped_removes) == 2


# ---------------------------------------------------------------------------
# Force rebalance tests
# ---------------------------------------------------------------------------


class TestForceRebalance:
    def test_force_rebalance_ignores_churn_limit(self):
        active = _active_members("AAPL", "OLD1", "OLD2")  # size = 3
        candidate = _candidate_members(
            ("AAPL", 50, None),
            ("OLD1", 700, None),
            ("OLD2", 800, None),
            ("NEW1", 100, None),
            ("NEW2", 200, None),
        )
        config = _default_config(
            max_churn_pct=0.10,  # would block all changes normally
            retain_until_rank_greater_than=650,
            add_only_if_rank_less_than_or_equal=400,
            force_rebalance=True,
        )
        result = _run(active_members=active, candidate_members=candidate, config=config)
        assert "OLD1" in result.removed
        assert "OLD2" in result.removed
        assert "NEW1" in result.added
        assert "NEW2" in result.added
        assert result.skipped_adds == []
        assert result.skipped_removes == []

    def test_force_rebalance_flag_reflected_in_metadata(self):
        active = _active_members("AAPL")
        candidate = _candidate_members(("AAPL", 50, None))
        config = _default_config(force_rebalance=True)
        result = _run(active_members=active, candidate_members=candidate, config=config)
        assert (
            result.proposed_version.generation_metadata_json["rebalance_config"]["force_rebalance"]
            is True
        )


# ---------------------------------------------------------------------------
# Target size tests
# ---------------------------------------------------------------------------


class TestTargetSize:
    def test_target_size_enforced_by_removing_lowest_ranked(self):
        """If proposed set exceeds target, lowest-ranked symbols are cut."""
        active = _active_members("A", "B", "C")  # all retain (ranks 1,2,3)
        candidate = _candidate_members(
            ("A", 1, None),
            ("B", 2, None),
            ("C", 3, None),
            ("D", 100, None),
            ("E", 200, None),
        )
        config = _default_config(
            target_universe_size=4,
            max_churn_pct=1.0,
            add_only_if_rank_less_than_or_equal=400,
        )
        result = _run(active_members=active, candidate_members=candidate, config=config)
        # 3 retained + 2 added = 5 → cut to 4
        assert len(result.retained) + len(result.added) == 4

    def test_proposed_universe_does_not_exceed_target_size(self):
        active = _active_members(*[f"OLD{i}" for i in range(10)])
        candidate = _candidate_members(*[(f"NEW{i}", i + 1, None) for i in range(20)])
        config = _default_config(
            target_universe_size=5,
            max_churn_pct=1.0,
            add_only_if_rank_less_than_or_equal=400,
        )
        result = _run(active_members=active, candidate_members=candidate, config=config)
        included = [m for m in result.proposed_members if m.excluded_reason is None]
        assert len(included) <= 5


# ---------------------------------------------------------------------------
# Proposed version persistence tests
# ---------------------------------------------------------------------------


class TestProposedVersionMetadata:
    def test_proposed_version_has_candidate_version_id_in_metadata(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        meta = result.proposed_version.generation_metadata_json
        assert meta["candidate_universe_version_id"] == _CANDIDATE_ID

    def test_proposed_version_effective_from_matches_candidate(self):
        candidate_version = _make_version(
            _CANDIDATE_ID, UniverseStatus.CANDIDATE, effective_from=_NOW
        )
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
            candidate_version=candidate_version,
        )
        assert result.proposed_version.effective_from == _NOW

    def test_proposed_version_config_hash_is_deterministic(self):
        active = _active_members("AAPL")
        candidate = _candidate_members(("AAPL", 50, None), ("MSFT", 100, None))
        config = _default_config()
        r1 = _run(active_members=active, candidate_members=candidate, config=config)
        r2 = _run(active_members=active, candidate_members=candidate, config=config)
        assert r1.proposed_version.config_hash == r2.proposed_version.config_hash

    def test_rebalance_run_contains_candidate_and_active_version_ids(self):
        active_version = _make_version(_ACTIVE_ID, UniverseStatus.ACTIVE)
        result = _run(
            active_version=active_version,
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        assert result.rebalance_run.candidate_universe_version_id == _CANDIDATE_ID
        assert result.rebalance_run.previous_universe_version_id == _ACTIVE_ID

    def test_rebalance_run_proposed_version_id_matches(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        assert (
            result.rebalance_run.proposed_universe_version_id
            == result.proposed_version.universe_version_id
        )

    def test_rebalance_run_status_is_accepted(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        assert result.rebalance_run.status == "accepted"

    def test_rebalance_run_churn_pct_matches_result(self):
        result = _run(
            active_members=_active_members("AAPL", "GONE"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
            config=_default_config(max_churn_pct=1.0),
        )
        assert result.rebalance_run.churn_pct == pytest.approx(result.churn_pct, abs=1e-6)

    def test_rebalance_run_symbol_lists_match_result(self):
        result = _run(
            active_members=_active_members("AAPL", "GONE"),
            candidate_members=_candidate_members(
                ("AAPL", 50, None), ("MSFT", 100, None), ("GONE", None, "delisted")
            ),
            config=_default_config(max_churn_pct=1.0),
        )
        assert set(result.rebalance_run.added_symbols_json) == set(result.added)
        assert set(result.rebalance_run.removed_symbols_json) == set(result.removed)
        assert set(result.rebalance_run.retained_symbols_json) == set(result.retained)


# ---------------------------------------------------------------------------
# Audit summary tests
# ---------------------------------------------------------------------------


class TestAuditSummary:
    def test_audit_summary_contains_all_required_keys(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        required = {
            "previous_universe_size",
            "candidate_universe_size",
            "proposed_universe_size",
            "added_count",
            "removed_count",
            "retained_count",
            "churn_pct",
            "churn_limited",
            "top_additions",
            "top_removals",
        }
        assert required.issubset(result.audit_summary.keys())

    def test_audit_summary_sizes_are_correct(self):
        result = _run(
            active_members=_active_members("AAPL", "MSFT", "GONE"),
            candidate_members=_candidate_members(
                ("AAPL", 50, None), ("MSFT", 100, None), ("NEW1", 200, None)
            ),
            config=_default_config(max_churn_pct=1.0),
        )
        assert result.audit_summary["previous_universe_size"] == 3
        assert result.audit_summary["removed_count"] == 1  # GONE
        assert result.audit_summary["added_count"] == 1  # NEW1
        assert result.audit_summary["retained_count"] == 2  # AAPL + MSFT

    def test_audit_summary_churn_limited_true_when_limited(self):
        active = _active_members("AAPL")
        candidate = _candidate_members(("AAPL", 50, None), ("MSFT", 100, None))
        config = _default_config(max_churn_pct=0.01)  # effectively 0 changes
        result = _run(active_members=active, candidate_members=candidate, config=config)
        assert result.audit_summary["churn_limited"] is True

    def test_audit_summary_churn_limited_false_when_not_limited(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
        )
        assert result.audit_summary["churn_limited"] is False


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_proposed_members_sorted_alphabetically(self):
        symbols = ["ZETA", "AAPL", "MSFT", "NFLX", "GOOG"]
        candidate = _candidate_members(*[(s, i + 1, None) for i, s in enumerate(symbols)])
        result = _run(
            active_version=None,
            active_members=[],
            candidate_members=candidate,
            config=_default_config(target_universe_size=100),
        )
        included = [m for m in result.proposed_members if m.excluded_reason is None]
        included_symbols = [m.symbol for m in included]
        assert included_symbols == sorted(included_symbols)

    def test_added_list_is_sorted(self):
        result = _run(
            active_members=_active_members("AAPL"),
            candidate_members=_candidate_members(
                ("AAPL", 50, None), ("ZETA", 100, None), ("AMZN", 200, None)
            ),
        )
        assert result.added == sorted(result.added)

    def test_removed_list_is_sorted(self):
        result = _run(
            active_members=_active_members("AAPL", "ZETA", "AMZN"),
            candidate_members=_candidate_members(("AAPL", 50, None)),
            config=_default_config(max_churn_pct=1.0),
        )
        assert result.removed == sorted(result.removed)


# ---------------------------------------------------------------------------
# Service-level tests (mocked session)
# ---------------------------------------------------------------------------


class TestUniverseRebalanceServiceIntegration:
    def test_propose_rebalance_raises_when_candidate_not_found(self):
        session = MagicMock()
        with patch(
            "autonomous_trading_platform.universe.services.universe_rebalance_service.UniverseVersionRepository"
        ) as MockRepo:
            repo_instance = MockRepo.return_value
            repo_instance.get_by_version_id.return_value = None

            svc = UniverseRebalanceService(session)
            with pytest.raises(ValueError, match="not found"):
                svc.propose_rebalance(
                    active_version_id=None,
                    candidate_version_id="missing-id",
                    config=_default_config(),
                )

    def test_propose_rebalance_raises_when_active_not_found(self):
        session = MagicMock()
        candidate_version = _make_version(_CANDIDATE_ID, UniverseStatus.CANDIDATE)

        with patch(
            "autonomous_trading_platform.universe.services.universe_rebalance_service.UniverseVersionRepository"
        ) as MockRepo:
            repo_instance = MockRepo.return_value
            repo_instance.get_by_version_id.side_effect = lambda vid: (
                candidate_version if vid == _CANDIDATE_ID else None
            )
            repo_instance.get_members.return_value = []

            svc = UniverseRebalanceService(session)
            with pytest.raises(ValueError, match="not found"):
                svc.propose_rebalance(
                    active_version_id="missing-active",
                    candidate_version_id=_CANDIDATE_ID,
                    config=_default_config(),
                )

    def test_propose_rebalance_succeeds_with_valid_inputs(self):
        session = MagicMock()
        active_version = _make_version(_ACTIVE_ID, UniverseStatus.ACTIVE)
        candidate_version = _make_version(_CANDIDATE_ID, UniverseStatus.CANDIDATE)
        active_members_rows = _active_members("AAPL", "MSFT")
        candidate_member_rows = _candidate_members(("AAPL", 50, None), ("MSFT", 100, None))

        with patch(
            "autonomous_trading_platform.universe.services.universe_rebalance_service.UniverseVersionRepository"
        ) as MockRepo:
            repo_instance = MockRepo.return_value
            repo_instance.get_by_version_id.side_effect = lambda vid: (
                active_version if vid == _ACTIVE_ID else candidate_version
            )
            repo_instance.get_members.return_value = candidate_member_rows
            repo_instance.get_included_members.return_value = active_members_rows

            svc = UniverseRebalanceService(session)
            result = svc.propose_rebalance(
                active_version_id=_ACTIVE_ID,
                candidate_version_id=_CANDIDATE_ID,
                config=_default_config(),
            )

        assert isinstance(result, RebalanceResult)
        assert result.proposed_version.status == UniverseStatus.PROPOSED
        assert set(result.retained) == {"AAPL", "MSFT"}


# ---------------------------------------------------------------------------
# Retained-by-churn-limit tests
# ---------------------------------------------------------------------------


class TestRetainedByChurnLimit:
    def test_degraded_symbol_kept_when_budget_exhausted(self):
        """A degraded symbol (rank > threshold) stays if budget is exhausted."""
        active = _active_members("A", "B", "C", "D")  # size = 4
        candidate = _candidate_members(
            ("A", 50, None),
            ("B", 700, None),  # optional remove (rank > 650)
            ("C", 100, None),
            # D missing → mandatory remove
        )
        config = _default_config(
            retain_until_rank_greater_than=650,
            max_churn_pct=0.25,  # max 1 change → D consumes it
        )
        result = _run(active_members=active, candidate_members=candidate, config=config)
        # B should be retained (churn limit), D removed (mandatory)
        assert "B" in result.retained
        assert "D" in result.removed

    def test_retained_by_churn_limit_has_correct_reason(self):
        active = _active_members("A", "B", "C", "D")
        candidate = _candidate_members(
            ("A", 50, None),
            ("B", 700, None),
            ("C", 100, None),
        )
        config = _default_config(
            retain_until_rank_greater_than=650,
            max_churn_pct=0.25,  # 1 change max, D (mandatory) uses it
        )
        result = _run(active_members=active, candidate_members=candidate, config=config)
        b_member = next((m for m in result.proposed_members if m.symbol == "B"), None)
        assert b_member is not None
        assert b_member.included_reason == "retained_by_churn_limit"
