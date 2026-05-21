#!/usr/bin/env python
"""
Regime-conditioned analysis CLI.

Loads persisted simulation artifacts for a given run and regime classification
feature data, runs regime analysis, and prints a summary.

Usage examples:

    python scripts/analyze_regimes.py \\
        --run-id <run_id> \\
        --experiment-id <experiment_id> \\
        --strategy-id <strategy_id> \\
        --dataset-version v1 \\
        --output json

    python scripts/analyze_regimes.py \\
        --run-id <run_id> \\
        --experiment-id <experiment_id> \\
        --strategy-id <strategy_id> \\
        --dataset-version v1 \\
        --output human \\
        --dimension trend

Options:
    --run-id              Simulation run UUID
    --experiment-id       Experiment identifier
    --strategy-id         Strategy identifier
    --dataset-version     Bar dataset version (e.g. v1)
    --stage-name          Stage name (default: adhoc)
    --window-role         Window role (default: default)
    --dimension           Limit output to a single regime dimension
    --output              Output format: human | json | yaml (default: human)
    --data-dir            Base data directory (default: data)
    --no-persist          Skip persisting regime analysis artifacts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow.dataset as ds

if TYPE_CHECKING:
    from autonomous_trading_platform.research.analysis.regimes.regime_analysis_result import (
        RegimeAnalysisResult,
    )


def _load_simulation_frame(
    base_path: Path,
    *,
    artifact_type: str,
    experiment_id: str,
    run_id: str,
    stage_name: str,
    window_role: str,
) -> pd.DataFrame:
    root = base_path / "simulations" / artifact_type
    if not root.exists():
        return pd.DataFrame()

    dataset = ds.dataset(str(root), format="parquet", partitioning="hive")
    expr = (
        (ds.field("experiment_id") == experiment_id)
        & (ds.field("run_id") == run_id)
        & (ds.field("stage_name") == stage_name)
        & (ds.field("window_role") == window_role)
    )
    table = dataset.to_table(filter=expr)
    return table.to_pandas()


def _load_regime_data(base_path: Path) -> pd.DataFrame:
    root = base_path / "features" / "regime_classification"
    if not root.exists():
        return pd.DataFrame()
    dataset = ds.dataset(str(root), format="parquet", partitioning="hive")
    return dataset.to_table().to_pandas()


def _print_human(result: RegimeAnalysisResult, dimension: str | None) -> None:
    from autonomous_trading_platform.research.analysis.regimes.regime_bucket import (
        REGIME_DIMENSIONS,
    )

    summary = result.summary()
    print(f"\n{'=' * 60}")
    print(f"Regime Analysis: {summary['strategy_id']}")
    print(f"Run:             {summary['run_id']}")
    print(f"Experiment:      {summary['experiment_id']}")
    print(f"{'=' * 60}")
    print(f"Overall sensitivity:  {summary['overall_sensitivity']:.4f}")
    print(f"Regime robust:        {summary['is_regime_robust']}")
    print()

    dims = [dimension] if dimension else REGIME_DIMENSIONS

    for dim in dims:
        summary_obj = {
            "trend": result.profile.by_trend,
            "volatility": result.profile.by_volatility,
            "liquidity": result.profile.by_liquidity,
            "mean_reversion": result.profile.by_mean_reversion,
            "risk": result.profile.by_risk,
        }.get(dim)

        if summary_obj is None:
            continue

        s = summary_obj.sensitivity
        print(f"  [{dim.upper()}]")
        print(f"    Best regime:    {s.best_regime or 'n/a'}")
        print(f"    Worst regime:   {s.worst_regime or 'n/a'}")
        print(f"    Sharpe range:   {s.sharpe_range:.4f}")
        print(f"    Robustness:     {s.regime_robustness:.4f}")
        print()

        for label, m in summary_obj.metrics_by_label.items():
            sharpe_str = f"{m.sharpe:.4f}" if m.sharpe is not None else "  n/a"
            wr_str = f"{m.win_rate:.2%}" if m.win_rate is not None else "  n/a"
            print(
                f"    {label:<22}  bars={m.bar_count:>5}  "
                f"exp={m.exposure_fraction:.1%}  "
                f"sharpe={sharpe_str}  winrate={wr_str}"
            )
        print()

    if result.transition_analyses:
        print("  [TRANSITIONS]")
        for dim_name, ta in result.transition_analyses.items():
            if dimension and dim_name != dimension:
                continue
            print(f"    {dim_name}: {ta.transition_count} transitions")
            if ta.avg_pre_transition_return is not None:
                print(f"      avg pre-transition return:  {ta.avg_pre_transition_return:.6f}")
            if ta.avg_post_transition_return is not None:
                print(f"      avg post-transition return: {ta.avg_post_transition_return:.6f}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime-conditioned simulation analysis")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--stage-name", default="adhoc")
    parser.add_argument("--window-role", default="default")
    parser.add_argument("--dimension", default=None)
    parser.add_argument("--output", choices=["human", "json", "yaml"], default="human")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    base_path = Path(args.data_dir)

    equity_curve = _load_simulation_frame(
        base_path,
        artifact_type="equity_curve",
        experiment_id=args.experiment_id,
        run_id=args.run_id,
        stage_name=args.stage_name,
        window_role=args.window_role,
    )
    trade_logs = _load_simulation_frame(
        base_path,
        artifact_type="trade_logs",
        experiment_id=args.experiment_id,
        run_id=args.run_id,
        stage_name=args.stage_name,
        window_role=args.window_role,
    )
    regime_data = _load_regime_data(base_path)

    if equity_curve.empty:
        print(
            f"ERROR: No equity curve found for run_id={args.run_id!r}, "
            f"experiment_id={args.experiment_id!r}",
            file=sys.stderr,
        )
        return 1

    if regime_data.empty:
        print("WARNING: No regime classification data found. Analysis will have no regime labels.")

    from autonomous_trading_platform.research.analysis.regimes.regime_analysis_repository import (
        RegimeAnalysisParquetRepository,
    )
    from autonomous_trading_platform.research.analysis.regimes.regime_analysis_service import (
        RegimeAnalysisRequest,
        RegimeAnalysisService,
    )
    from autonomous_trading_platform.research.simulation.artifact_identity import (
        SimulationArtifactIdentity,
    )

    identity = SimulationArtifactIdentity(
        run_id=args.run_id,
        experiment_id=args.experiment_id,
        strategy_id=args.strategy_id,
        dataset_version=args.dataset_version,
        stage_name=args.stage_name,
        window_role=args.window_role,
    )

    repository = (
        None if args.no_persist else RegimeAnalysisParquetRepository(base_path=args.data_dir)
    )
    service = RegimeAnalysisService(repository=repository)

    request = RegimeAnalysisRequest(
        equity_curve=equity_curve,
        trade_logs=trade_logs if not trade_logs.empty else pd.DataFrame(),
        regime_data=regime_data,
        identity=identity,
    )

    result = service.analyze(request, persist=not args.no_persist)

    if args.output == "human":
        _print_human(result, args.dimension)
    elif args.output == "json":
        print(json.dumps(result.summary(), indent=2, default=str))
    elif args.output == "yaml":
        import yaml  # type: ignore[import]

        print(yaml.dump(result.summary(), default_flow_style=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
