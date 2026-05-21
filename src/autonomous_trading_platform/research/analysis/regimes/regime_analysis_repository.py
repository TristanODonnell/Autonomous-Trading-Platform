from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    REGIME_ANALYSIS_METRICS_DATASET,
    REGIME_TRANSITION_SUMMARY_DATASET,
    STRATEGY_REGIME_PROFILE_DATASET,
    ParquetDataset,
)

from .regime_analysis_result import RegimeAnalysisResult


def _today() -> date:
    return datetime.now(tz=UTC).date()


class RegimeAnalysisParquetRepository:
    def __init__(self, *, base_path: str = "data") -> None:
        self.base_path = Path(base_path)

    def _dataset_root(self, dataset: ParquetDataset) -> Path:
        root = self.base_path
        for part in dataset.root_parts:
            root = root / part
        return root

    def _write(self, *, table: pa.Table, dataset: ParquetDataset) -> None:
        table = table.cast(dataset.schema)
        root = self._dataset_root(dataset)
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

    def _identity_dict(self, identity: SimulationArtifactIdentity) -> dict[str, str]:
        return {
            "run_id": identity.run_id,
            "experiment_id": identity.experiment_id,
            "strategy_id": identity.strategy_id,
            "stage_name": identity.stage_name,
            "window_role": identity.window_role,
            "dataset_version": identity.dataset_version,
        }

    def persist(
        self,
        result: RegimeAnalysisResult,
        *,
        identity: SimulationArtifactIdentity,
    ) -> None:
        self._persist_metrics(result, identity=identity)
        self._persist_transitions(result, identity=identity)
        self._persist_profiles(result, identity=identity)

    def _persist_metrics(
        self,
        result: RegimeAnalysisResult,
        *,
        identity: SimulationArtifactIdentity,
    ) -> None:
        if not result.all_regime_metrics:
            return

        today = _today()
        base = self._identity_dict(identity)
        rows: list[dict[str, Any]] = []

        for m in result.all_regime_metrics:
            rows.append(
                {
                    **base,
                    "regime_dimension": m.bucket.dimension,
                    "regime_label": m.bucket.label,
                    "bar_count": m.bar_count,
                    "exposure_fraction": m.exposure_fraction,
                    "total_return": m.total_return,
                    "cagr": m.cagr,
                    "sharpe": m.sharpe,
                    "sortino": m.sortino,
                    "volatility": m.volatility,
                    "max_drawdown": m.max_drawdown,
                    "trade_count": m.trade_count,
                    "win_rate": m.win_rate,
                    "expectancy": m.expectancy,
                    "profit_factor": m.profit_factor,
                    "avg_win": m.avg_win,
                    "avg_loss": m.avg_loss,
                    "trade_frequency": m.trade_frequency,
                    "avg_bar_return": m.avg_bar_return,
                    "date": today,
                }
            )

        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=REGIME_ANALYSIS_METRICS_DATASET)

    def _persist_transitions(
        self,
        result: RegimeAnalysisResult,
        *,
        identity: SimulationArtifactIdentity,
    ) -> None:
        if not result.transition_analyses:
            return

        today = _today()
        base = self._identity_dict(identity)
        rows: list[dict[str, Any]] = []

        for dimension, analysis in result.transition_analyses.items():
            pair_counts: dict[tuple[str, str], list[dict[str, float | None]]] = {}
            for tw in analysis.transition_windows:
                key = (tw.transition.from_regime, tw.transition.to_regime)
                pair_counts.setdefault(key, []).append(
                    {
                        "duration_before": float(tw.transition.duration_before),
                        "pre_return": tw.pre_return,
                        "post_return": tw.post_return,
                    }
                )

            for (from_r, to_r), windows in pair_counts.items():
                durations: list[float] = [float(w["duration_before"]) for w in windows]  # type: ignore[arg-type]
                pre_rets: list[float] = [
                    float(w["pre_return"]) for w in windows if w["pre_return"] is not None
                ]  # type: ignore[arg-type]
                post_rets: list[float] = [
                    float(w["post_return"]) for w in windows if w["post_return"] is not None
                ]  # type: ignore[arg-type]

                rows.append(
                    {
                        **base,
                        "regime_dimension": dimension,
                        "from_regime": from_r,
                        "to_regime": to_r,
                        "transition_count": len(windows),
                        "avg_duration_before": sum(durations) / len(durations)
                        if durations
                        else None,
                        "avg_pre_return": sum(pre_rets) / len(pre_rets) if pre_rets else None,
                        "avg_post_return": sum(post_rets) / len(post_rets) if post_rets else None,
                        "date": today,
                    }
                )

        if not rows:
            return

        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=REGIME_TRANSITION_SUMMARY_DATASET)

    def _persist_profiles(
        self,
        result: RegimeAnalysisResult,
        *,
        identity: SimulationArtifactIdentity,
    ) -> None:
        profile = result.profile
        today = _today()
        base = self._identity_dict(identity)
        rows: list[dict[str, Any]] = []

        for dim_name, summary in {
            "trend": profile.by_trend,
            "volatility": profile.by_volatility,
            "liquidity": profile.by_liquidity,
            "mean_reversion": profile.by_mean_reversion,
            "risk": profile.by_risk,
        }.items():
            s = summary.sensitivity
            rows.append(
                {
                    **base,
                    "regime_dimension": dim_name,
                    "sharpe_std": s.sharpe_std,
                    "sharpe_range": s.sharpe_range,
                    "best_regime": s.best_regime,
                    "worst_regime": s.worst_regime,
                    "regime_robustness": s.regime_robustness,
                    "overall_sensitivity": profile.overall_sensitivity,
                    "is_regime_robust": profile.is_regime_robust,
                    "date": today,
                }
            )

        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)
        self._write(table=table, dataset=STRATEGY_REGIME_PROFILE_DATASET)
