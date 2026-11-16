"""Which evidence the deciding scores may rest on (plan items R5 and R16).

Three scores decide something after the research pipeline: the robustness
score (``overall_passed``), the candidate composite (ranking order) and the
deployability score (which survivors get a governance record). All three read
the same six robustness components. This module says which of those
components count.

Default: walk-forward consistency and Monte Carlo stability, and both must be
present. Those are the two checks whose gates still eliminate strategies. The
regime, shock-stress and overfitting components are computed and reported, but
their gates are report-only (``enforce=False``), so they carry no weight: a
measure not trusted to eliminate a strategy is not trusted to promote one.
Parameter stability is never supplied today and carries no weight either.

When a gate is switched back on, add its component to ``deciding`` here (and to
``required`` if a strategy without it should get no score). The policy and the
gates' ``enforce`` flags are set in different places; a test in
``tests/research/test_research_stages_smoke.py`` fails if the replay hook's
gates and this default disagree.

A strategy missing a required component gets no score: the overall and the
composite are 0.0, ``evidence_complete`` is False, and it is never deployable.
Before this, a missing component's weight was spread over the others, so a
strategy with less evidence could score higher than one with more.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

WALK_FORWARD = "walk_forward_consistency"
MONTE_CARLO = "mc_stability"
REGIME = "regime_robustness"
PARAMETER_STABILITY = "parameter_stability"
STRESS = "stress_resilience"
OVERFITTING = "overfitting_resistance"

#: Every robustness component, in the order reports list them.
COMPONENTS: tuple[str, ...] = (
    WALK_FORWARD,
    MONTE_CARLO,
    REGIME,
    PARAMETER_STABILITY,
    STRESS,
    OVERFITTING,
)


@dataclass(frozen=True)
class EvidencePolicy:
    """
    deciding   Components that carry weight in the deciding scores.
    required   Components that must be present for any score to be given.
               Always a subset of ``deciding``.
    """

    deciding: frozenset[str] = frozenset({WALK_FORWARD, MONTE_CARLO})
    required: frozenset[str] = frozenset({WALK_FORWARD, MONTE_CARLO})

    def __post_init__(self) -> None:
        unknown = (self.deciding | self.required) - set(COMPONENTS)
        if unknown:
            raise ValueError(f"EvidencePolicy: unknown component(s) {sorted(unknown)}")
        if not self.required <= self.deciding:
            raise ValueError(
                "EvidencePolicy: required components must also be deciding, got "
                f"{sorted(self.required - self.deciding)}"
            )

    @classmethod
    def unrestricted(cls) -> EvidencePolicy:
        """Every component decides and none is required (the behaviour before 2026-11)."""
        return cls(deciding=frozenset(COMPONENTS), required=frozenset())

    def decides(self, component: str) -> bool:
        return component in self.deciding

    def missing(self, present: Iterable[str]) -> tuple[str, ...]:
        """Required components absent from ``present``, in report order."""
        have = set(present)
        return tuple(c for c in COMPONENTS if c in self.required and c not in have)

    def reported_only(self, present: Iterable[str]) -> tuple[str, ...]:
        """Components that were supplied but carry no weight, in report order."""
        have = set(present)
        return tuple(c for c in COMPONENTS if c in have and c not in self.deciding)


DEFAULT_EVIDENCE_POLICY = EvidencePolicy()
