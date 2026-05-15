from datetime import UTC, datetime, timedelta

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.contracts.runtime.universe_version import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.universe_version import (
    UNIVERSE_MEMBER_RULES,
    UNIVERSE_VERSION_RULES,
    is_valid_transition,
)


def make_universe_version(**overrides) -> UniverseVersion:
    start = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    data = {
        "universe_version_id": "uv-001",
        "name": "test_universe",
        "source": UniverseSource.CUSTOM,
        "created_at": start,
        "effective_from": start,
        "effective_to": start + timedelta(days=1),
        "status": UniverseStatus.ACTIVE,
        "rebalance_reason": None,
        "config_hash": "abc123",
    }
    data.update(overrides)
    return UniverseVersion(**data)


def make_universe_member(**overrides) -> UniverseMember:
    data = {
        "universe_version_id": "uv-001",
        "symbol": "AAPL",
        "rank": 0,
        "score": None,
        "included_reason": None,
        "excluded_reason": None,
        "liquidity_metrics_json": None,
        "quality_metrics_json": None,
    }
    data.update(overrides)
    return UniverseMember(**data)


class TestUniverseVersionRules:
    def test_valid_version_passes(self) -> None:
        result = run_rules(make_universe_version(), UNIVERSE_VERSION_RULES)
        assert result.ok
        assert result.violations == []

    def test_invalid_status_fails(self) -> None:
        version = make_universe_version(status="unknown_status")
        result = run_rules(version, UNIVERSE_VERSION_RULES)
        assert not result.ok
        assert any(v.code == "STATUS_VALID" for v in result.violations)

    def test_effective_window_order_invalid_fails(self) -> None:
        start = datetime(2025, 1, 2, 10, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
        version = make_universe_version(effective_from=start, effective_to=end)
        result = run_rules(version, UNIVERSE_VERSION_RULES)
        assert not result.ok
        assert any(v.code == "EFFECTIVE_WINDOW_ORDER_VALID" for v in result.violations)

    def test_effective_to_none_is_valid(self) -> None:
        version = make_universe_version(effective_to=None)
        result = run_rules(version, UNIVERSE_VERSION_RULES)
        assert result.ok

    def test_empty_config_hash_fails(self) -> None:
        version = make_universe_version(config_hash="")
        result = run_rules(version, UNIVERSE_VERSION_RULES)
        assert not result.ok
        assert any(v.code == "CONFIG_HASH_NON_EMPTY" for v in result.violations)

    def test_empty_name_fails(self) -> None:
        version = make_universe_version(name="")
        result = run_rules(version, UNIVERSE_VERSION_RULES)
        assert not result.ok
        assert any(v.code == "NAME_NON_EMPTY" for v in result.violations)

    def test_empty_source_fails(self) -> None:
        version = make_universe_version(source="")
        result = run_rules(version, UNIVERSE_VERSION_RULES)
        assert not result.ok
        assert any(v.code == "SOURCE_NON_EMPTY" for v in result.violations)

    def test_all_statuses_are_valid(self) -> None:
        for status in UniverseStatus:
            version = make_universe_version(status=status.value)
            result = run_rules(version, UNIVERSE_VERSION_RULES)
            assert result.ok, (
                f"Status '{status.value}' should be valid but got: {result.violations}"
            )


class TestUniverseMemberRules:
    def test_valid_member_passes(self) -> None:
        result = run_rules(make_universe_member(), UNIVERSE_MEMBER_RULES)
        assert result.ok
        assert result.violations == []

    def test_invalid_symbol_format_fails(self) -> None:
        member = make_universe_member(symbol="bad symbol")
        result = run_rules(member, UNIVERSE_MEMBER_RULES)
        assert not result.ok
        assert any(v.code == "SYMBOL_FORMAT_VALID" for v in result.violations)

    def test_lowercase_symbol_fails(self) -> None:
        member = make_universe_member(symbol="aapl")
        result = run_rules(member, UNIVERSE_MEMBER_RULES)
        assert not result.ok

    def test_valid_symbol_formats(self) -> None:
        for symbol in ["AAPL", "BRK.B", "BTC-USD", "SPY_1"]:
            member = make_universe_member(symbol=symbol)
            result = run_rules(member, UNIVERSE_MEMBER_RULES)
            assert result.ok, f"Symbol '{symbol}' should be valid"

    def test_negative_rank_fails(self) -> None:
        member = make_universe_member(rank=-1)
        result = run_rules(member, UNIVERSE_MEMBER_RULES)
        assert not result.ok
        assert any(v.code == "RANK_NON_NEGATIVE" for v in result.violations)

    def test_zero_rank_passes(self) -> None:
        member = make_universe_member(rank=0)
        result = run_rules(member, UNIVERSE_MEMBER_RULES)
        assert result.ok

    def test_none_rank_passes(self) -> None:
        member = make_universe_member(rank=None)
        result = run_rules(member, UNIVERSE_MEMBER_RULES)
        assert result.ok


class TestStatusTransitions:
    def test_candidate_can_transition_to_proposed(self) -> None:
        assert is_valid_transition(UniverseStatus.CANDIDATE, UniverseStatus.PROPOSED)

    def test_candidate_can_transition_to_active(self) -> None:
        assert is_valid_transition(UniverseStatus.CANDIDATE, UniverseStatus.ACTIVE)

    def test_proposed_can_transition_to_active(self) -> None:
        assert is_valid_transition(UniverseStatus.PROPOSED, UniverseStatus.ACTIVE)

    def test_proposed_can_transition_to_retired(self) -> None:
        assert is_valid_transition(UniverseStatus.PROPOSED, UniverseStatus.RETIRED)

    def test_active_can_transition_to_retired(self) -> None:
        assert is_valid_transition(UniverseStatus.ACTIVE, UniverseStatus.RETIRED)

    def test_active_cannot_transition_to_candidate(self) -> None:
        assert not is_valid_transition(UniverseStatus.ACTIVE, UniverseStatus.CANDIDATE)

    def test_active_cannot_transition_to_proposed(self) -> None:
        assert not is_valid_transition(UniverseStatus.ACTIVE, UniverseStatus.PROPOSED)

    def test_retired_has_no_valid_transitions(self) -> None:
        for status in UniverseStatus:
            assert not is_valid_transition(UniverseStatus.RETIRED, status)
