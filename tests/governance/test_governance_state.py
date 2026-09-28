# tests/governance/test_governance_state.py

from __future__ import annotations

import pytest

from autonomous_trading_platform.application.services.strategy_governance_service import (
    _STATE_ALIASES,
)
from autonomous_trading_platform.governance.models.governance_state import (
    GovernanceState,
    is_valid_transition,
)


def test_candidate_is_the_post_research_state() -> None:
    assert GovernanceState.CANDIDATE.value == "candidate"
    assert is_valid_transition(GovernanceState.PROPOSED, GovernanceState.CANDIDATE)
    assert is_valid_transition(GovernanceState.CANDIDATE, GovernanceState.APPROVED_PAPER)


@pytest.mark.parametrize("legacy", ["approved_research", "APPROVED_RESEARCH"])
def test_legacy_approved_research_value_resolves_to_candidate(legacy: str) -> None:
    assert GovernanceState(legacy) is GovernanceState.CANDIDATE


def test_unknown_value_still_raises() -> None:
    with pytest.raises(ValueError):
        GovernanceState("not_a_state")


@pytest.mark.parametrize("alias", ["approved_research", "research", "candidate"])
def test_governance_service_aliases_normalize_to_candidate(alias: str) -> None:
    assert _STATE_ALIASES[alias] == "candidate"
