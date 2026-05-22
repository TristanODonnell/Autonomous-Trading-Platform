from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DeterministicSeedInputs:
    base_seed: int
    experiment_id: str
    strategy_id: str | None = None
    config_hash: str | None = None
    stage_name: str | None = None
    window_role: str | None = None
    fold_id: int | str | None = None
    trial_id: int | str | None = None


class DeterministicSeedService:
    """Derive per-unit seeds from stable research-unit identity fields."""

    _MAX_NUMPY_SEED = (2**32) - 1

    def derive_seed(self, inputs: DeterministicSeedInputs) -> int:
        if not isinstance(inputs.base_seed, int):
            raise ValueError("base_seed must be an int")
        if inputs.base_seed < 0:
            raise ValueError("base_seed must be >= 0")

        payload = {
            "base_seed": inputs.base_seed,
            "config_hash": inputs.config_hash,
            "experiment_id": inputs.experiment_id,
            "fold_id": inputs.fold_id,
            "stage_name": inputs.stage_name,
            "strategy_id": inputs.strategy_id,
            "trial_id": inputs.trial_id,
            "window_role": inputs.window_role,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % self._MAX_NUMPY_SEED
