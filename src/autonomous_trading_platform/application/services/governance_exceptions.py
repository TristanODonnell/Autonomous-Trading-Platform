from __future__ import annotations


class MissingSourceRunError(Exception):
    """Raised when a capital-bearing promotion is attempted without an explicit source_run_id.

    Promotions into approved_paper or approved_live must reference the specific simulation run
    whose metrics justified the transition.  Without this reference the audit trail cannot
    answer "which run justified this promotion?" and the metrics source is ambiguous.
    """

    def __init__(self, strategy_id: str, target_state: str) -> None:
        self.strategy_id = strategy_id
        self.target_state = target_state
        super().__init__(
            f"Promotion of strategy {strategy_id!r} to {target_state!r} requires an explicit "
            "source_run_id. Capital-bearing promotions must reference the specific evaluation "
            "run that justifies the transition. "
            "Missing field: source_run_id."
        )


class PromotionRulesMissingError(Exception):
    """Raised when no active PromotionRules row exists for a requested promotion transition.

    Promotion fails closed — a missing rule is treated as a configuration gap, not a pass.
    """

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"No active PromotionRules row for transition {from_state!r} -> {to_state!r}. "
            "Promotion blocked: fail-closed on missing configuration."
        )


class PromotionCriteriaConfigurationError(Exception):
    """Raised when a PromotionRules row exists but has null values for criteria that are
    required for the target state.

    Null required thresholds silently bypass governance checks.  This exception prevents
    that by refusing to treat 'not configured' as 'no constraint'.

    Future extension points:
    - per-tier required criteria
    - operator-approval gate for configuration errors
    - severity levels (warning vs hard block)
    """

    def __init__(
        self,
        from_state: str,
        to_state: str,
        missing_fields: list[str],
        rule_id: str | None = None,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.missing_fields = missing_fields
        self.rule_id = rule_id
        super().__init__(
            f"PromotionRules for {from_state!r} -> {to_state!r} "
            f"(rule_id={rule_id!r}) has null values for required criteria: "
            f"{missing_fields}. Promotion blocked."
        )
