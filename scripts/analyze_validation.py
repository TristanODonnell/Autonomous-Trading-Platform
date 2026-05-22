#!/usr/bin/env python
"""
Advanced validation framework CLI.

Loads persisted simulation artifacts and runs the validation pipeline,
printing a robustness summary with scores for each validation stage.

Usage examples:

    # Full validation from persisted artifacts
    python scripts/analyze_validation.py \\
        --experiment-id my-experiment \\
        --strategy-id strat_001 \\
        --dataset-version v1 \\
        --run-id <uuid> \\
        --output human

    # Stress-test only (no walk-forward data needed)
    python scripts/analyze_validation.py \\
        --experiment-id my-experiment \\
        --strategy-id strat_001 \\
        --dataset-version v1 \\
        --run-id <uuid> \\
        --mode stress \\
        --output json

    # Overfitting analysis only
    python scripts/analyze_validation.py \\
        --experiment-id my-experiment \\
        --strategy-id strat_001 \\
        --dataset-version v1 \\
        --run-id <uuid> \\
        --mode overfitting \\
        --output human

Options:
    --experiment-id     Experiment identifier
    --strategy-id       Strategy identifier
    --dataset-version   Dataset version (e.g. v1)
    --run-id            Simulation run UUID
    --stage-name        Artifact stage name (default: adhoc)
    --window-role       Artifact window role (default: default)
    --mode              all | stress | overfitting | walk-forward (default: all)
    --output            human | json | yaml (default: human)
    --data-dir          Base data directory (default: data)
    --no-persist        Skip persisting validation artifacts
    --min-sharpe        Min Sharpe survival threshold for stress tests (default: 0.0)
    --max-drawdown      Max drawdown survival threshold for stress tests (default: -0.40)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds


def _load_sim_frame(
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
    try:
        dataset = ds.dataset(str(root), format="parquet", partitioning="hive")
        expr = (
            (ds.field("experiment_id") == experiment_id)
            & (ds.field("run_id") == run_id)
            & (ds.field("stage_name") == stage_name)
            & (ds.field("window_role") == window_role)
        )
        return dataset.to_table(filter=expr).to_pandas()
    except Exception:
        return pd.DataFrame()


def _print_human(summary: dict, mode: str) -> None:
    rs = summary.get("robustness_score", {})
    print(f"\n{'=' * 60}")
    print(f"Validation Summary: {summary.get('strategy_id', 'n/a')}")
    print(f"Experiment:         {summary.get('experiment_id', 'n/a')}")
    print(f"Validation Run:     {summary.get('validation_run_id', 'n/a')}")
    print(f"{'=' * 60}")
    print(f"Overall Passed:     {summary.get('overall_passed', False)}")
    print(f"Robustness Score:   {rs.get('overall', 0.0):.4f}")
    print()

    components = rs.get("components", {})
    if components:
        print("  [ROBUSTNESS COMPONENTS]")
        for name, val in components.items():
            val_str = f"{val:.4f}" if val is not None else "  n/a"
            print(f"    {name:<30} {val_str}")
        print()

    stages = summary.get("stages", {})
    if stages:
        print("  [STAGE RESULTS]")
        for stage_name, sr in stages.items():
            status = "PASS" if sr.get("passed") else "FAIL"
            score = sr.get("score", 0.0)
            print(f"    [{status}] {stage_name:<25} score={score:.4f}")
            stg_summary = sr.get("summary", {})
            for k, v in stg_summary.items():
                if isinstance(v, (dict, list)):
                    continue
                print(f"           {k}: {v}")
            for w in sr.get("warnings", []):
                print(f"      WARN: {w}")
        print()

    global_warnings = summary.get("warnings", [])
    if global_warnings:
        print("  [WARNINGS]")
        for w in set(global_warnings):
            print(f"    * {w}")
        print()

    print("  [SCORE EXPLANATION]")
    for line in rs.get("explanation", []):
        print(f"    {line}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Advanced validation framework CLI")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage-name", default="adhoc")
    parser.add_argument("--window-role", default="default")
    parser.add_argument(
        "--mode",
        choices=["all", "stress", "overfitting", "walk-forward"],
        default="all",
    )
    parser.add_argument("--output", choices=["human", "json", "yaml"], default="human")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--min-sharpe", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float, default=-0.40)
    args = parser.parse_args()

    base_path = Path(args.data_dir)

    equity_curve = _load_sim_frame(
        base_path,
        artifact_type="equity_curve",
        experiment_id=args.experiment_id,
        run_id=args.run_id,
        stage_name=args.stage_name,
        window_role=args.window_role,
    )

    if equity_curve.empty:
        print(
            f"ERROR: No equity curve found for run_id={args.run_id!r} "
            f"experiment_id={args.experiment_id!r}",
            file=sys.stderr,
        )
        return 1

    trade_logs = _load_sim_frame(
        base_path,
        artifact_type="trade_logs",
        experiment_id=args.experiment_id,
        run_id=args.run_id,
        stage_name=args.stage_name,
        window_role=args.window_role,
    )

    from autonomous_trading_platform.research.validation.stress_test_service import (
        StressTestService,
    )
    from autonomous_trading_platform.research.validation.validation_artifact_repository import (
        ValidationArtifactRepository,
    )
    from autonomous_trading_platform.research.validation.validation_orchestrator import (
        ValidationConfig,
        ValidationOrchestrator,
        ValidationRequest,
    )

    enable_wf = args.mode in ("all", "walk-forward")
    enable_stress = args.mode in ("all", "stress")
    enable_overfit = args.mode in ("all", "overfitting")

    stress_svc = StressTestService(
        min_sharpe_threshold=args.min_sharpe,
        max_drawdown_threshold=args.max_drawdown,
    )

    repo = None if args.no_persist else ValidationArtifactRepository(base_path=args.data_dir)

    orchestrator = ValidationOrchestrator(
        stress_test_service=stress_svc,
        artifact_repository=repo,
    )

    config = ValidationConfig(
        enable_walk_forward=enable_wf,
        enable_stress_tests=enable_stress,
        enable_survivorship_check=True,
        enable_overfitting_analysis=enable_overfit,
        enable_parameter_sensitivity=False,
        min_acceptable_sharpe=args.min_sharpe,
        max_drawdown_threshold=args.max_drawdown,
    )

    trade_count = len(trade_logs) if not trade_logs.empty else None

    request = ValidationRequest(
        strategy_id=args.strategy_id,
        experiment_id=args.experiment_id,
        dataset_version=args.dataset_version,
        equity_curve=equity_curve,
        trade_count=trade_count,
    )

    summary = orchestrator.run_validation(request, config)
    summary_dict = summary.as_dict()

    if args.output == "human":
        _print_human(summary_dict, args.mode)
    elif args.output == "json":
        print(json.dumps(summary_dict, indent=2, default=str))
    elif args.output == "yaml":
        import yaml  # type: ignore[import]

        print(yaml.dump(summary_dict, default_flow_style=False))

    return 0 if summary.overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
