# autonomous_trading_platform/governance/deployment/deployment_registry.py

"""
TASK-186 — Deployment Registry
================================
Tracks which strategies are currently deployed in paper and live environments.

Responsibilities
----------------
* deploy()   — register a new deployment (gate + RBAC checked first).
* pause()    — suspend trading for a deployed strategy without removing it.
* stop()     — fully stop and remove a strategy from an environment.
* rollback() — revert to the previous deployment record for a strategy.
* query helpers — get_deployment, list_active, is_deployed.

Design decisions
----------------
* State is held in a dict keyed by (strategy_id, environment).  In the first
  implementation this is in-process memory.  Swap ``_store`` for a
  repository backed by Postgres when persistence is needed (TASK-186 follow-up).
* The gate (TASK-187), role checker (TASK-189 stub) and audit logger
  (TASK-188 no-op) are all injected — no concrete dependency on any of them.
* DeploymentRecord is immutable (Pydantic BaseModel).  Mutations produce new
  records; old ones are kept in ``_history`` for the rollback use-case.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from autonomous_trading_platform.governance.deployment.audit_logger import (
    DeploymentAuditLogger,
    NoOpDeploymentAuditLogger,
)
from autonomous_trading_platform.governance.deployment.deployment_gate import DeploymentGate
from autonomous_trading_platform.governance.deployment.role_checker import (
    DeploymentAction,
    DeploymentRoleChecker,
    StubDeploymentRoleChecker,
)
from autonomous_trading_platform.governance.exceptions.deployment_exceptions import (
    DeploymentGateError,  # re-exported for callers  # noqa: F401
    DeploymentNotFoundError,
    DeploymentPermissionError,
)
from autonomous_trading_platform.governance.models.deployment_models import (
    DeploymentEnvironment,
    DeploymentRecord,
    DeploymentStatus,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState

if TYPE_CHECKING:
    pass

_DeployKey = tuple[str, DeploymentEnvironment]  # (strategy_id, environment)


class DeploymentRegistry:
    """
    Central registry for strategy deployments.

    Parameters
    ----------
    gate:
        Stateless gate that enforces governance-state requirements.
        Defaults to ``DeploymentGate()``.
    role_checker:
        RBAC checker.  Defaults to ``StubDeploymentRoleChecker`` (always permits).
        Swap for a real implementation when TASK-189 lands.
    audit_logger:
        Audit logger.  Defaults to ``NoOpDeploymentAuditLogger`` (deferred, TASK-188).
    """

    def __init__(
        self,
        gate: DeploymentGate | None = None,
        role_checker: DeploymentRoleChecker | None = None,
        audit_logger: DeploymentAuditLogger | None = None,
    ) -> None:
        self._gate = gate or DeploymentGate()
        self._role_checker: DeploymentRoleChecker = role_checker or StubDeploymentRoleChecker()
        self._audit: DeploymentAuditLogger = audit_logger or NoOpDeploymentAuditLogger()

        # Active deployments: one record per (strategy_id, environment)
        self._store: dict[_DeployKey, DeploymentRecord] = {}

        # Full history per key — used by rollback()
        self._history: dict[_DeployKey, list[DeploymentRecord]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deploy(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
        current_governance_state: GovernanceState,
        actor: str,
        metadata: dict | None = None,
    ) -> DeploymentRecord:
        """
        Deploy a strategy to an environment.

        Checks (in order):
        1. DeploymentGate — governance state must match environment requirement.
        2. DeploymentRoleChecker — actor must have DEPLOY permission.

        Raises:
            DeploymentGateError: governance state not sufficient.
            DeploymentPermissionError: actor lacks permission.
        """
        # 1. Gate check (TASK-187)
        self._gate.assert_deployable(
            strategy_id=strategy_id,
            environment=environment,
            current_state=current_governance_state,
        )

        # 2. RBAC check (TASK-189 stub)
        self._assert_permitted(actor, DeploymentAction.DEPLOY, strategy_id)

        now = datetime.now(tz=UTC)
        record = DeploymentRecord(
            deployment_id=str(uuid.uuid4()),
            strategy_id=strategy_id,
            environment=environment,
            status=DeploymentStatus.ACTIVE,
            governance_state_at_deploy=current_governance_state,
            deployed_by=actor,
            deployed_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

        key = (strategy_id, environment)
        # Push any previous record into history before replacing it
        if key in self._store:
            self._history[key].append(self._store[key])
        self._store[key] = record

        self._audit.log_deployed(record, actor)
        return record

    def pause(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
        actor: str,
    ) -> DeploymentRecord:
        """
        Pause an active deployment.  The record stays in the registry with
        status=PAUSED so it can be resumed or stopped later.

        Raises:
            DeploymentNotFoundError: no active/paused deployment for this key.
            DeploymentPermissionError: actor lacks permission.
        """
        self._assert_permitted(actor, DeploymentAction.PAUSE, strategy_id)
        record = self._get_or_raise(strategy_id, environment)
        updated = self._update_status(record, DeploymentStatus.PAUSED)
        self._store[(strategy_id, environment)] = updated
        self._audit.log_paused(updated, actor)
        return updated

    def stop(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
        actor: str,
    ) -> DeploymentRecord:
        """
        Stop a deployment and remove it from the active store.
        The final record is preserved in history.

        Raises:
            DeploymentNotFoundError: no deployment for this key.
            DeploymentPermissionError: actor lacks permission.
        """
        self._assert_permitted(actor, DeploymentAction.STOP, strategy_id)
        record = self._get_or_raise(strategy_id, environment)
        stopped = self._update_status(record, DeploymentStatus.STOPPED)

        key = (strategy_id, environment)
        self._history[key].append(stopped)
        del self._store[key]

        self._audit.log_stopped(stopped, actor)
        return stopped

    def rollback(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
        actor: str,
    ) -> DeploymentRecord:
        """
        Revert to the most recent previous deployment record for this
        (strategy_id, environment) pair.

        The governance state stored in the previous record is restored as-is —
        the caller is responsible for ensuring the strategy's current governance
        state still matches (or re-running the gate check externally).

        Raises:
            DeploymentNotFoundError: no history to roll back to.
            DeploymentPermissionError: actor lacks permission.
        """
        self._assert_permitted(actor, DeploymentAction.ROLLBACK, strategy_id)

        key = (strategy_id, environment)
        history = self._history.get(key, [])
        if not history:
            raise DeploymentNotFoundError(strategy_id=strategy_id, environment=environment)

        previous = history.pop()
        current_deployment_id = self._store[key].deployment_id if key in self._store else None

        # Archive current record before replacing
        if key in self._store:
            self._history[key].append(self._store[key])

        now = datetime.now(tz=UTC)
        restored = cast(
            DeploymentRecord,
            previous.model_copy(
                update={
                    "deployment_id": str(uuid.uuid4()),
                    "updated_at": now,
                    "status": DeploymentStatus.ACTIVE,
                    "rolled_back_from_deployment_id": current_deployment_id,
                }
            ),
        )
        self._store[key] = restored
        self._audit.log_rollback(
            restored,
            previous_deployment_id=previous.deployment_id,
            actor=actor,
        )
        return restored

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_deployment(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
    ) -> DeploymentRecord | None:
        """Return the active deployment record, or None if not deployed."""
        return self._store.get((strategy_id, environment))

    def is_deployed(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
    ) -> bool:
        return (strategy_id, environment) in self._store

    def list_active(
        self,
        environment: DeploymentEnvironment | None = None,
    ) -> list[DeploymentRecord]:
        """
        Return all active deployment records, optionally filtered by environment.
        Includes both ACTIVE and PAUSED statuses (strategy is still registered).
        """
        records = list(self._store.values())
        if environment is not None:
            records = [r for r in records if r.environment == environment]
        return records

    def deployment_history(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
    ) -> list[DeploymentRecord]:
        """Return all historical records for this (strategy_id, environment) pair."""
        return list(self._history.get((strategy_id, environment), []))

    # ------------------------------------------------------------------
    # Execution-engine integration point (TASK-187)
    # ------------------------------------------------------------------

    def assert_strategy_is_deployed_and_active(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
    ) -> DeploymentRecord:
        """
        Guard method for use by the execution engine.

        Confirms that:
        * The strategy has an active deployment record in *environment*.
        * Its status is ACTIVE (not PAUSED or STOPPED).

        Raises:
            DeploymentNotFoundError: not deployed or has been stopped.
            RuntimeError: deployed but currently paused.
        """
        record = self.get_deployment(strategy_id, environment)
        if record is None:
            raise DeploymentNotFoundError(strategy_id=strategy_id, environment=environment)
        if record.status == DeploymentStatus.PAUSED:
            raise RuntimeError(
                f"Strategy '{strategy_id}' is paused in '{environment.value}' "
                f"and cannot execute orders until resumed."
            )
        return record

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_raise(
        self,
        strategy_id: str,
        environment: DeploymentEnvironment,
    ) -> DeploymentRecord:
        record = self._store.get((strategy_id, environment))
        if record is None:
            raise DeploymentNotFoundError(strategy_id=strategy_id, environment=environment)
        return record

    def _assert_permitted(
        self,
        actor: str,
        action: DeploymentAction,
        strategy_id: str,
    ) -> None:
        if not self._role_checker.can_perform(actor, action, strategy_id):
            raise DeploymentPermissionError(
                actor=actor,
                action=action.value,
                strategy_id=strategy_id,
            )

    @staticmethod
    def _update_status(record: DeploymentRecord, status: DeploymentStatus) -> DeploymentRecord:
        return cast(
            DeploymentRecord,
            record.model_copy(update={"status": status, "updated_at": datetime.now(tz=UTC)}),
        )
