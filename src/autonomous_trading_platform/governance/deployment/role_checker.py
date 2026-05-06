# autonomous_trading_platform/governance/deployment/role_checker.py

"""
TASK-189 — Role-Based Access Control for Deployment Actions (STUB)
===================================================================
Full RBAC is deferred.  This module provides:

1.  ``DeploymentAction`` — the set of actions that will eventually be gated.
2.  ``DeploymentRoleChecker`` — a Protocol so callers (DeploymentRegistry, the
    execution engine, etc.) depend on an *interface*, not a concrete class.
3.  ``StubDeploymentRoleChecker`` — always permits every action.
    Wire this in now; swap it for a real implementation when RBAC lands.

When RBAC is implemented, create a class that satisfies the Protocol, inject it
via DeploymentRegistry.__init__, and delete this stub.
"""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable


class DeploymentAction(enum.StrEnum):
    DEPLOY = "deploy"
    PAUSE = "pause"
    STOP = "stop"
    ROLLBACK = "rollback"


@runtime_checkable
class DeploymentRoleChecker(Protocol):
    """
    Protocol for RBAC checks on deployment actions.

    Implementations must be thread-safe and should raise
    ``DeploymentPermissionError`` (or return False) when access is denied.
    """

    def can_perform(
        self,
        actor: str,
        action: DeploymentAction,
        strategy_id: str,
    ) -> bool:
        """Return True if *actor* may perform *action* on *strategy_id*."""
        ...


class StubDeploymentRoleChecker:
    """
    STUB — always grants permission.

    Replace with a real implementation backed by your RBAC store before
    going to production.  The stub is intentionally loud about what it is
    so it cannot be accidentally left in place silently.

    TODO(TASK-189): implement real role lookup against the RBAC store.
    """

    _STUB_WARNING = (
        "StubDeploymentRoleChecker is active — all deployment actions are permitted "
        "regardless of actor role.  Replace before production."
    )

    def __init__(self) -> None:
        import warnings

        warnings.warn(self._STUB_WARNING, stacklevel=2)

    def can_perform(
        self,
        actor: str,  # noqa: ARG002
        action: DeploymentAction,  # noqa: ARG002
        strategy_id: str,  # noqa: ARG002
    ) -> bool:
        return True
