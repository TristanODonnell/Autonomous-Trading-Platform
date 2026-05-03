# autonomous_trading_platform/governance/exceptions/deployment_exceptions.py

from __future__ import annotations

from autonomous_trading_platform.governance.models.deployment_models import DeploymentEnvironment
from autonomous_trading_platform.governance.models.governance_state import GovernanceState


class DeploymentGateError(Exception):
    """Raised when a strategy fails the deployment gate check (TASK-187)."""

    def __init__(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
        current_state: GovernanceState,
        required_state: GovernanceState,
    ) -> None:
        self.strategy_id = strategy_id
        self.environment = environment
        self.current_state = current_state
        self.required_state = required_state
        super().__init__(
            f"Strategy '{strategy_id}' cannot be deployed to {environment.value!r}: "
            f"governance state is '{current_state.value}', "
            f"requires '{required_state.value}'."
        )


class DeploymentPermissionError(Exception):
    """Raised when an actor lacks permission for a deployment action (TASK-189 stub)."""

    def __init__(self, actor: str, action: str, strategy_id: str) -> None:
        self.actor = actor
        self.action = action
        self.strategy_id = strategy_id
        super().__init__(
            f"Actor '{actor}' is not permitted to perform '{action}' on strategy '{strategy_id}'."
        )


class DeploymentNotFoundError(Exception):
    """Raised when a deployment_id or strategy/environment pair does not exist."""

    def __init__(self, strategy_id: str, environment: DeploymentEnvironment) -> None:
        super().__init__(
            f"No active deployment found for strategy '{strategy_id}' "
            f"in environment '{environment.value}'."
        )
