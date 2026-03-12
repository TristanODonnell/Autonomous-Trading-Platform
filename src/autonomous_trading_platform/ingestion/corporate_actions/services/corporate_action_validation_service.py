from __future__ import annotations

from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.validators.core import ValidationResult, run_rules
from autonomous_trading_platform.contracts.validators.corporate_action import CORPORATE_ACTION_RULES


class CorporateActionValidationService:
    def validate(self, corporate_action: CorporateAction) -> ValidationResult:
        result = run_rules(corporate_action, CORPORATE_ACTION_RULES)
        self._validate_complex_checks(corporate_action, result)
        return result

    def _validate_complex_checks(
        self,
        action: CorporateAction,
        result: ValidationResult,
    ) -> None:
        # add checks here that are too awkward for rules
        pass
