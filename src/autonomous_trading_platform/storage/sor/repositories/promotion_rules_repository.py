# autonomous_trading_platform/storage/sor/repositories/promotion_rules_repository.py

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class PromotionRulesRepository(BaseRepository):
    def get_by_rule_id(self, rule_id: str) -> PromotionRules | None:
        stmt = select(PromotionRules).where(PromotionRules.rule_id == rule_id)
        return cast(PromotionRules | None, self.session.scalars(stmt).one_or_none())

    def get_rules_for_transition(
        self,
        from_status: str,
        to_status: str,
    ) -> PromotionRules | None:
        stmt = select(PromotionRules).where(
            PromotionRules.from_status == from_status,
            PromotionRules.to_status == to_status,
            PromotionRules.is_active.is_(True),
        )
        return cast(PromotionRules | None, self.session.scalars(stmt).one_or_none())

    def get_all_active(self) -> list[PromotionRules]:
        stmt = select(PromotionRules).where(PromotionRules.is_active.is_(True))
        return list(self.session.scalars(stmt).all())

    def insert(self, row: PromotionRules) -> None:
        self.session.add(row)

    def deactivate(self, rule_id: str) -> None:

        obj = self.get_by_rule_id(rule_id)
        if obj is not None:
            obj.is_active = False
