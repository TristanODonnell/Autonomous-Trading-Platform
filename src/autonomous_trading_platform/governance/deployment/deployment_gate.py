# autonomous_trading_platform/governance/deployment/deployment_gate.py

"""
TASK-187 — Deployment Gate
==========================
Enforces the rule that only strategies in the correct governance state may be
deployed to each environment:

    paper  → requires GovernanceState.APPROVED_PAPER
    live   → requires GovernanceState.APPROVED_LIVE

This class is intentionally stateless so it can be used both inside
DeploymentRegistry and directly by the execution engine as a standalone guard.

Usage (execution engine)::

    gate = DeploymentGate()
    gate.assert_deployable(
        strategy_id="strat-123",
        environment=DeploymentEnvironment.LIVE,
        current_state=record.current_state,
    )
"""

from __future__ import annotations

from autonomous_trading_platform.governance.exceptions.deployment_exceptions import (
    DeploymentGateError,
)
from autonomous_trading_platform.governance.models.deployment_models import DeploymentEnvironment
from autonomous_trading_platform.governance.models.governance_state import GovernanceState

# Maps each deployable environment to the exact governance state required.
_REQUIRED_STATE: dict[DeploymentEnvironment, GovernanceState] = {
    DeploymentEnvironment.PAPER: GovernanceState.APPROVED_PAPER,
    DeploymentEnvironment.LIVE: GovernanceState.APPROVED_LIVE,
}


class DeploymentGate:
    """Stateless guard — call assert_deployable before any deploy action."""

    def assert_deployable(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
        current_state: GovernanceState,
    ) -> None:
        """
        Assert that *current_state* satisfies the requirement for *environment*.

        Raises:
            DeploymentGateError: if the strategy is not in the required state.
        """
        required = _REQUIRED_STATE[environment]
        if current_state != required:
            raise DeploymentGateError(
                strategy_id=strategy_id,
                environment=environment,
                current_state=current_state,
                required_state=required,
            )

    def is_deployable(
        self,
        environment: DeploymentEnvironment,
        current_state: GovernanceState,
    ) -> bool:
        """Non-raising convenience check. Useful for bulk filtering."""
        return bool(current_state == _REQUIRED_STATE[environment])

    @staticmethod
    def required_state_for(environment: DeploymentEnvironment) -> GovernanceState:
        """Return the governance state required to deploy to *environment*."""
        return _REQUIRED_STATE[environment]
