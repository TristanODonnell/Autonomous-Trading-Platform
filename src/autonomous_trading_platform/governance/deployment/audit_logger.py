# autonomous_trading_platform/governance/deployment/audit_logger.py

"""
Full audit logging is deferred.  This module defines the ``DeploymentAuditLogger``
Protocol so every call-site already passes an audit logger through dependency
injection — swapping in a real implementation later requires zero changes to
DeploymentRegistry or any other caller.

The ``NoOpDeploymentAuditLogger`` is the placeholder used until TASK-188 is
implemented.

TODO(TASK-188): implement a real logger that persists deployment events
(deploy, pause, stop, rollback, environment switch) to Postgres with
actor, timestamp, strategy_id, environment and rationale.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from autonomous_trading_platform.governance.models.deployment_models import (
    DeploymentEnvironment,
    DeploymentRecord,
)


@runtime_checkable
class DeploymentAuditLogger(Protocol):
    """
    Protocol for recording deployment lifecycle events.

    All methods are fire-and-forget from the caller's perspective — implementations
    must not raise on logging errors (log-and-swallow or circuit-break internally).
    """

    def log_deployed(self, record: DeploymentRecord, actor: str, **kwargs: Any) -> None: ...
    def log_paused(self, record: DeploymentRecord, actor: str, **kwargs: Any) -> None: ...
    def log_stopped(self, record: DeploymentRecord, actor: str, **kwargs: Any) -> None: ...
    def log_rollback(
        self,
        record: DeploymentRecord,
        previous_deployment_id: str,
        actor: str,
        **kwargs: Any,
    ) -> None: ...
    def log_environment_switch(
        self,
        strategy_id: str,
        from_env: DeploymentEnvironment,
        to_env: DeploymentEnvironment,
        actor: str,
        **kwargs: Any,
    ) -> None: ...


class NoOpDeploymentAuditLogger:
    def log_deployed(self, record: DeploymentRecord, actor: str, **kwargs: Any) -> None:
        pass

    def log_paused(self, record: DeploymentRecord, actor: str, **kwargs: Any) -> None:
        pass

    def log_stopped(self, record: DeploymentRecord, actor: str, **kwargs: Any) -> None:
        pass

    def log_rollback(
        self,
        record: DeploymentRecord,
        previous_deployment_id: str,
        actor: str,
        **kwargs: Any,
    ) -> None:
        pass

    def log_environment_switch(
        self,
        strategy_id: str,
        from_env: DeploymentEnvironment,
        to_env: DeploymentEnvironment,
        actor: str,
        **kwargs: Any,
    ) -> None:
        pass
