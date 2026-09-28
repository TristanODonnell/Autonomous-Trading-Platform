from __future__ import annotations

import enum


class GovernanceState(enum.StrEnum):
    PROPOSED = "proposed"
    # Passed research; sits on the bench until promoted to paper. Formerly "approved_research".
    CANDIDATE = "candidate"
    APPROVED_PAPER = "approved_paper"
    APPROVED_LIVE = "approved_live"
    REJECTED = "rejected"
    RETIRED = "retired"

    @classmethod
    def _missing_(cls, value: object) -> GovernanceState | None:
        # Accept the pre-rename value from older artifacts, fixtures and API clients.
        if isinstance(value, str) and value.lower() == LEGACY_CANDIDATE_STATE:
            return cls.CANDIDATE
        return None


LEGACY_CANDIDATE_STATE = "approved_research"


# Valid transitions: from_state -> set of allowed to_states
ALLOWED_TRANSITIONS: dict[GovernanceState, set[GovernanceState]] = {
    GovernanceState.PROPOSED: {
        GovernanceState.CANDIDATE,
        GovernanceState.REJECTED,
    },
    GovernanceState.CANDIDATE: {
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
