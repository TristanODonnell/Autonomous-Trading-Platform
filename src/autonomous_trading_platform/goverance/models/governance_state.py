from __future__ import annotations

import enum


class GovernanceState(enum.StrEnum):
    PROPOSED = "proposed"
    APPROVED_RESEARCH = "approved_research"
    APPROVED_PAPER = "approved_paper"
    APPROVED_LIVE = "approved_live"
    REJECTED = "rejected"
    RETIRED = "retired"


# Valid transitions: from_state -> set of allowed to_states
ALLOWED_TRANSITIONS: dict[GovernanceState, set[GovernanceState]] = {
    GovernanceState.PROPOSED: {
        GovernanceState.APPROVED_RESEARCH,
        GovernanceState.REJECTED,
    },
    GovernanceState.APPROVED_RESEARCH: {
        GovernanceState.APPROVED_PAPER,
        GovernanceState.REJECTED,
        GovernanceState.RETIRED,
    },
    GovernanceState.APPROVED_PAPER: {
        GovernanceState.APPROVED_LIVE,
        GovernanceState.REJECTED,
        GovernanceState.RETIRED,
    },
    GovernanceState.APPROVED_LIVE: {
        GovernanceState.RETIRED,
        GovernanceState.REJECTED,
    },
    GovernanceState.REJECTED: {
        GovernanceState.PROPOSED,  # allow re-submission
    },
    GovernanceState.RETIRED: set(),  # terminal
}


def is_valid_transition(from_state: GovernanceState, to_state: GovernanceState) -> bool:
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())
