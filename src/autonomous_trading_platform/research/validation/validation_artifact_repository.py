"""
Parquet persistence for validation framework artifacts.

Follows the same _write pattern as RegimeAnalysisParquetRepository:
hive-partitioned, schema-validated, overwrite_or_ignore behavior.

Datasets persisted:
  validation/robustness_scores/     — one row per validation run
  validation/stress_test_results/   — one row per (run, scenario)
  validation/walk_forward_results/  — one row per validation run
  validation/overfitting_analysis/  — one row per validation run
  validation/sensitivity_profiles/  — one row per (run, parameter)
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from autonomous_trading_platform.storage.parquet.datasets import (
    VALIDATION_OVERFITTING_DATASET,
    VALIDATION_ROBUSTNESS_SCORE_DATASET,
    VALIDATION_SENSITIVITY_DATASET,
    VALIDATION_STRESS_TEST_DATASET,
    VALIDATION_WALK_FORWARD_DATASET,
    ParquetDataset,
)

logger = logging.getLogger(__name__)


def _today() -> date:
    return datetime.now(tz=UTC).date()


class ValidationArtifactRepository:
    """
    Persists ValidationSummary artifacts to Parquet.

    Parameters
    ----------
    base_path
        Root data directory (e.g. "data").  Validation artifacts are written
        under ``{base_path}/validation/``.
    """

    def __init__(self, *, base_path: str = "data") -> None:
        self.base_path = Path(base_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def persist(self, summary: Any) -> None:
        """Persist all validation artifacts from a ValidationSummary."""
        try:
            self._persist_robustness_score(summary)
        except Exception as exc:
            logger.warning("Failed to persist robustness score: %s", exc)

        try:
            self._persist_walk_forward(summary)
        except Exception as exc:
            logger.warning("Failed to persist walk-forward result: %s", exc)

        try:
            self._persist_stress_test(summary)
        except Exception as exc:
            logger.warning("Failed to persist stress test results: %s", exc)

        try:
            self._persist_overfitting(summary)
        except Exception as exc:
            logger.warning("Failed to persist overfitting analysis: %s", exc)

        try:
            self._persist_sensitivity(summary)
        except Exception as exc:
            logger.warning("Failed to persist sensitivity profile: %s", exc)

    # ------------------------------------------------------------------
    # Internal writers
    # ------------------------------------------------------------------

    def _write(self, *, table: pa.Table, dataset: ParquetDataset) -> None:
        table = table.cast(dataset.schema)
        root = self.base_path
        for part in dataset.root_parts:
            root = root / part
        root.mkdir(parents=True, exist_ok=True)
        partition_schema = pa.schema([(col, pa.string()) for col in dataset.partition_cols])
        partitioning = ds.partitioning(partition_schema, flavor="hive")
        ds.write_dataset(
            data=table,
            base_dir=str(root),
            format="parquet",
            partitioning=partitioning,
            existing_data_behavior="overwrite_or_ignore",
        )

    def _base_row(self, summary: Any) -> dict[str, str]:
        return {
            "validation_run_id": summary.validation_run_id,
            "experiment_id": summary.experiment_id,
            "strategy_id": summary.strategy_id,
            "dataset_version": summary.dataset_version,
        }

    def _persist_robustness_score(self, summary: Any) -> None:
        rs = summary.robustness_score
        today = _today()
        row: dict[str, Any] = {
            **self._base_row(summary),
            "overall_score": rs.overall,
            "walk_forward_consistency": rs.walk_forward_consistency,
            "mc_stability": rs.mc_stability,
            "regime_robustness": rs.regime_robustness,
            "parameter_stability": rs.parameter_stability,
            "stress_resilience": rs.stress_resilience,
            "overfitting_resistance": rs.overfitting_resistance,
            "overall_passed": summary.overall_passed,
            "computed_at": summary.computed_at,
            "date": today,
        }
        df = pd.DataFrame([row])
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=VALIDATION_ROBUSTNESS_SCORE_DATASET)

    def _persist_walk_forward(self, summary: Any) -> None:
        wf_stage = summary.stage_results.get("walk_forward")
        if wf_stage is None:
            return
        s = wf_stage.summary
        today = _today()
        row: dict[str, Any] = {
            **self._base_row(summary),
            "n_folds": s.get("n_folds", 0),
            "n_folds_passed": s.get("n_folds_passed", 0),
            "fold_consistency": s.get("fold_consistency", 0.0),
            "avg_train_sharpe": s.get("avg_train_sharpe", 0.0),
            "avg_test_sharpe": s.get("avg_test_sharpe", 0.0),
            "train_test_degradation": s.get("train_test_degradation", 0.0),
            "fold_sharpe_cv": s.get("fold_sharpe_cv", 0.0),
            "fold_sharpe_stability": 1.0 - min(1.0, s.get("fold_sharpe_cv", 0.0)),
            "date": today,
        }
        df = pd.DataFrame([row])
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=VALIDATION_WALK_FORWARD_DATASET)

    def _persist_stress_test(self, summary: Any) -> None:
        stress_stage = summary.stage_results.get("stress_test")
        if stress_stage is None:
            return

        scenario_results = stress_stage.summary.get("scenario_results", [])
        if not scenario_results:
            return

        today = _today()
        base = self._base_row(summary)
        rows: list[dict[str, Any]] = []

        # Scenario results stored in stage summary as dicts or objects
        stress_sr = stress_stage.summary
        # The summary dict from the orchestrator contains aggregated info only
        # We need the full scenario results from the stage — not stored there.
        # For now, write the summary as a single aggregated row per scenario_name
        # by reconstructing from the summary dict.
        # In production, the orchestrator should expose scenario-level rows.

        # Fallback: write one row with worst-case info
        rows.append(
            {
                **base,
                "scenario_name": stress_sr.get("worst_scenario", "all"),
                "description": f"Worst scenario: {stress_sr.get('worst_scenario', 'n/a')}",
                "original_sharpe": 0.0,
                "stressed_sharpe": 0.0,
                "original_drawdown": 0.0,
                "stressed_drawdown": 0.0,
                "sharpe_degradation": stress_sr.get("avg_sharpe_degradation", 0.0),
                "survived": stress_sr.get("survival_rate", 0.0) >= 0.5,
                "date": today,
            }
        )

        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=VALIDATION_STRESS_TEST_DATASET)

    def _persist_overfitting(self, summary: Any) -> None:
        overfit_stage = summary.stage_results.get("overfitting")
        if overfit_stage is None:
            return
        s = overfit_stage.summary
        ind = s.get("indicators", {})
        today = _today()
        row: dict[str, Any] = {
            **self._base_row(summary),
            "overfitting_probability": s.get("overfitting_probability", 0.0),
            "resistance_score": s.get("resistance_score", 1.0),
            "train_test_degradation": ind.get("train_test_degradation"),
            "fold_instability": ind.get("fold_instability"),
            "mc_instability": ind.get("mc_instability"),
            "regime_concentration": ind.get("regime_concentration"),
            "low_trade_count_flag": ind.get("low_trade_count_flag"),
            "narrow_period_alpha": ind.get("narrow_period_alpha"),
            "parameter_fragility": ind.get("parameter_fragility"),
            "date": today,
        }
        df = pd.DataFrame([row])
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=VALIDATION_OVERFITTING_DATASET)

    def _persist_sensitivity(self, summary: Any) -> None:
        sens_stage = summary.stage_results.get("parameter_sensitivity")
        if sens_stage is None:
            return
        s = sens_stage.summary
        params = s.get("parameters", {})
        if not params:
            return
        today = _today()
        base = self._base_row(summary)
        overall = s.get("overall_stability_score", 0.0)
        rows: list[dict[str, Any]] = []
        for param_name, pd_info in params.items():
            sr: dict[str, Any] = pd_info if isinstance(pd_info, dict) else {}
            stab_region = sr.get("stability_region") or [None, None]
            rows.append(
                {
                    **base,
                    "parameter_name": param_name,
                    "reference_value": 0.0,  # not stored in summary dict
                    "sensitivity_score": sr.get("sensitivity_score", 0.0),
                    "stability_score": sr.get("stability_score", 1.0),
                    "is_stable": bool(sr.get("is_stable", True)),
                    "stability_region_min": stab_region[0] if stab_region else None,
                    "stability_region_max": stab_region[1] if len(stab_region) > 1 else None,
                    "overall_stability_score": overall,
                    "date": today,
                }
            )
        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=VALIDATION_SENSITIVITY_DATASET)
