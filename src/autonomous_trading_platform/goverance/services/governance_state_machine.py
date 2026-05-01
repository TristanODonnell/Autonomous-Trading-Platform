from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from autonomous_trading_platform.contracts.governance.strategy_governance import (
    StrategyGovernanceRecord,
)

from ..models.governance_state import GovernanceState, is_valid_transition


class InvalidGovernanceTransitionError(Exception):
    pass


class GovernanceStateMachine:
    def transition(
        self,
        record: StrategyGovernanceRecord,
        to_state: GovernanceState,
        actor: str,
    ) -> StrategyGovernanceRecord:
        if not is_valid_transition(record.current_state, to_state):
            raise InvalidGovernanceTransitionError(
                f"Cannot transition {record.strategy_id} from {record.current_state} → {to_state}"
            )
        return cast(
            StrategyGovernanceRecord,
            record.model_copy(update={"current_state": to_state, "updated_at": datetime.now(UTC)}),
        )

    @staticmethod
    def propose(
        strategy_id: str,
        config_hash: str,
        experiment_id: str,
        source_run_id,
        submitted_by: str = "system",
    ) -> StrategyGovernanceRecord:
        now = datetime.now(UTC)
        return StrategyGovernanceRecord(
            strategy_id=strategy_id,
            config_hash=config_hash,
            current_state=GovernanceState.PROPOSED,
            experiment_id=experiment_id,
            source_run_id=source_run_id,
            submitted_at=now,
            updated_at=now,
            submitted_by=submitted_by,
        )
