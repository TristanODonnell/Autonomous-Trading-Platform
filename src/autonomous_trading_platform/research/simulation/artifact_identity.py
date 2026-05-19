from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SimulationArtifactIdentity:
    """
    Canonical collision-safe identity for a single simulation artifact.

    Required fields guarantee every persisted artifact can be traced back to
    its originating run, stage, window, strategy, and dataset.  Optional fields
    capture richer context (seed, universe, config hash) when available.

    Produces deterministic hive-style partition paths so Parquet writes from
    walk-forward folds, Monte Carlo seeds, staged pipelines, and repeated
    strategy runs can never overwrite each other.
    """

    run_id: str
    experiment_id: str
    strategy_id: str
    dataset_version: str
    stage_name: str
    window_role: str
    # Optional enrichment — include when available
    seed: int | None = None
    window_id: str | None = None
    universe_version: str | None = None
    config_hash: str | None = None
    price_basis: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        missing = [
            name
            for name, val in [
                ("run_id", self.run_id),
                ("experiment_id", self.experiment_id),
                ("strategy_id", self.strategy_id),
                ("dataset_version", self.dataset_version),
                ("stage_name", self.stage_name),
                ("window_role", self.window_role),
            ]
            if not val
        ]
        if missing:
            raise ValueError(f"SimulationArtifactIdentity missing required fields: {missing}")

    def partition_path(self) -> str:
        """Deterministic hive-style partition path for this artifact."""
        return (
            f"experiment_id={self.experiment_id}/"
            f"run_id={self.run_id}/"
            f"stage_name={self.stage_name}/"
            f"window_role={self.window_role}/"
            f"strategy_id={self.strategy_id}/"
            f"dataset_version={self.dataset_version}"
        )

    def to_manifest_dict(self) -> dict:
        """Fields suitable for inclusion in an artifact manifest."""
        d: dict = {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "dataset_version": self.dataset_version,
            "stage_name": self.stage_name,
            "window_role": self.window_role,
        }
        if self.seed is not None:
            d["seed"] = self.seed
        if self.window_id is not None:
            d["window_id"] = self.window_id
        if self.universe_version is not None:
            d["universe_version"] = self.universe_version
        if self.config_hash is not None:
            d["config_hash"] = self.config_hash
        if self.price_basis is not None:
            d["price_basis"] = self.price_basis
        if self.start_date is not None:
            d["start_date"] = str(self.start_date)
        if self.end_date is not None:
            d["end_date"] = str(self.end_date)
        return d
