from __future__ import annotations

from autonomous_trading_platform.platform.replay.platform_replay_config import (
    TimelineEventConfig,
    build_typed_timeline_events,
)


def _governance_event(**extra: str) -> TimelineEventConfig:
    return TimelineEventConfig(
        at="2024-02-15",
        type="governance_manual_transition",
        strategy_id="mean_reversion_v1",
        to_state="candidate",
        **extra,
    )


def test_fixture_actor_role_reaches_the_governance_event() -> None:
    (event,) = build_typed_timeline_events(
        [_governance_event(actor_role="system_risk")], actor="replay"
    )

    assert event.actor_role == "system_risk"


def test_actor_role_defaults_to_operator() -> None:
    (event,) = build_typed_timeline_events([_governance_event()], actor="replay")

    assert event.actor_role == "operator"
