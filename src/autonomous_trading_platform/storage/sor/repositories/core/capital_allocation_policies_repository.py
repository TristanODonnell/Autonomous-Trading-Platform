# autonomous_trading_platform/storage/sor/repositories/capital_allocation_policies_repository.py

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class CapitalAllocationPoliciesRepository(BaseRepository):
    def get_by_policy_id(self, policy_id: str) -> CapitalAllocationPolicies | None:
        stmt = select(CapitalAllocationPolicies).where(
            CapitalAllocationPolicies.policy_id == policy_id
        )
        return cast(
            CapitalAllocationPolicies | None,
            self.session.scalars(stmt).one_or_none(),
        )

    def get_by_status(self, approval_status: str) -> list[CapitalAllocationPolicies]:
        stmt = select(CapitalAllocationPolicies).where(
            CapitalAllocationPolicies.approval_status == approval_status
        )
        return list(self.session.scalars(stmt).all())

    def get_active_policy(
        self,
        approval_status: str,
        performance_tier: str | None = None,
    ) -> CapitalAllocationPolicies | None:

        if performance_tier is not None:
            stmt = select(CapitalAllocationPolicies).where(
                CapitalAllocationPolicies.approval_status == approval_status,
                CapitalAllocationPolicies.performance_tier == performance_tier,
                CapitalAllocationPolicies.is_active.is_(True),
            )
            result = cast(
                CapitalAllocationPolicies | None,
                self.session.scalars(stmt).one_or_none(),
            )
            if result is not None:
                return result

        stmt = select(CapitalAllocationPolicies).where(
            CapitalAllocationPolicies.approval_status == approval_status,
            CapitalAllocationPolicies.performance_tier.is_(None),
            CapitalAllocationPolicies.is_active.is_(True),
        )
        return cast(
            CapitalAllocationPolicies | None,
            self.session.scalars(stmt).one_or_none(),
        )

    def insert(self, row: CapitalAllocationPolicies) -> None:
        self.session.add(row)

    def deactivate(self, policy_id: str) -> None:
        obj = self.get_by_policy_id(policy_id)
        if obj is not None:
            obj.is_active = False
