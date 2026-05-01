"""
StageRegistry — single dispatch point for loading pipeline stages from YAML.

Adding a new stage type requires only:
  1. Implement the stage class with from_dict() in its own file.
  2. Add one line to _REGISTRY below.
  3. Nothing else changes — CLI, tests, YAML format all stay the same.

Usage
-----
    from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry

    stages = [
        StageRegistry.load(stage_raw, simulation_runner)
        for stage_raw in pipeline_raw["stages"]
    ]
"""

from __future__ import annotations

from typing import Any

from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner

from .base_stage import BaseStage
from .simulation_stage import SimulationStage
from .walk_forward_stage import WalkForwardStage

# ---------------------------------------------------------------------------
# Registry — add new stage types here and nowhere else
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[BaseStage]] = {
    "simulation": SimulationStage,
    "walk_forward": WalkForwardStage,
    # "regime":       RegimeStage,        # future
    # "monte_carlo":  MonteCarloStage,    # future
}


class StageRegistry:
    @staticmethod
    def load(
        raw: dict[str, Any],
        simulation_runner: SimulationRunner,
    ) -> BaseStage:
        """
        Deserialize a single stage dict into a concrete BaseStage instance.

        The 'type' key in the dict controls which class is used.
        Defaults to 'simulation' if omitted — keeps old YAML configs working.

        Raises
        ------
        ValueError
            If the type is not registered.
        """
        stage_type = raw.get("type", "simulation")
        cls = _REGISTRY.get(stage_type)
        if cls is None:
            registered = ", ".join(sorted(_REGISTRY))
            raise ValueError(f"Unknown stage type '{stage_type}'. Registered types: {registered}")
        return cls.from_dict(raw, simulation_runner)

    @staticmethod
    def registered_types() -> list[str]:
        """Return all currently registered stage type names."""
        return sorted(_REGISTRY)
