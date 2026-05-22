#!/usr/bin/env python
"""
Research intelligence CLI (TASK-2.5).

Loads persisted validation and regime artifacts, runs the ML-assisted
research intelligence pipeline, and prints rankings, clusters, robustness
estimates, and overfit warnings.

Usage examples:

    # Analyse a single strategy
    python scripts/run_research_intelligence.py rank-strategies \\
        --experiment-id my-experiment \\
        --strategy-id strat_001 \\
        --dataset-version v1 \\
        --run-id <uuid> \\
        --output human

    # Cluster multiple strategies (provide strategy IDs as comma-separated list)
    python scripts/run_research_intelligence.py cluster-strategies \\
        --experiment-id my-experiment \\
        --strategy-ids strat_001,strat_002,strat_003 \\
        --dataset-version v1 \\
        --output json

    # Summarise research intelligence for a single strategy
    python scripts/run_research_intelligence.py summarize-research-intelligence \\
        --experiment-id my-experiment \\
        --strategy-id strat_001 \\
        --dataset-version v1 \\
        --run-id <uuid>

    # Analyse overfit risk for a single strategy
    python scripts/run_research_intelligence.py analyze-overfit-risk \\
        --experiment-id my-experiment \\
        --strategy-id strat_001 \\
        --dataset-version v1 \\
        --run-id <uuid> \\
        --output human

Options:
    --experiment-id     Experiment identifier
    --strategy-id       Single strategy identifier
    --strategy-ids      Comma-separated list of strategy IDs (for batch commands)
    --dataset-version   Dataset version (e.g. v1)
    --run-id            Simulation run UUID (optional for batch commands)
    --output            human | json | yaml (default: human)
    --data-dir          Base data directory (default: data)
    --no-persist        Skip persisting intelligence artifacts
    --family            Strategy family override (momentum|mean_reversion|trend|factor|composite)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_validation_summary(
    base_path: Path,
    *,
    experiment_id: str,
    strategy_id: str,
    run_id: str,
    dataset_version: str,
) -> Any | None:
    """Load the latest ValidationSummary from persisted Parquet, if available."""
    import pandas as pd
    import pyarrow.dataset as ds

    robustness_root = base_path / "validation" / "robustness_scores"
    if not robustness_root.exists():
        return None
    try:
        dataset = ds.dataset(str(robustness_root), format="parquet", partitioning="hive")
        expr = (ds.field("experiment_id") == experiment_id) & (
            ds.field("strategy_id") == strategy_id
        )
        df = dataset.to_table(filter=expr).to_pandas()
    except Exception:
        return None

    if df.empty:
        return None

    # Load other validation tables
    overfit_root = base_path / "validation" / "overfitting_analysis"
    wf_root = base_path / "validation" / "walk_forward_results"
    stress_root = base_path / "validation" / "stress_test_results"

    def _load_table(root: Path) -> pd.DataFrame:
        if not root.exists():
            return pd.DataFrame()
        try:
            dset = ds.dataset(str(root), format="parquet", partitioning="hive")
            return dset.to_table(
                filter=(
                    (ds.field("experiment_id") == experiment_id)
                    & (ds.field("strategy_id") == strategy_id)
                )
            ).to_pandas()
        except Exception:
            return pd.DataFrame()

    robustness_row = df.iloc[-1]
    overfit_df = _load_table(overfit_root)
    wf_df = _load_table(wf_root)
    stress_df = _load_table(stress_root)

    # Reconstruct a lightweight ValidationSummary-like object for the intelligence layer
    from types import SimpleNamespace

    rs = SimpleNamespace(
        overall=float(robustness_row.get("overall_score", 0.5)),
        walk_forward_consistency=_float_or_none(robustness_row, "walk_forward_consistency"),
        mc_stability=_float_or_none(robustness_row, "mc_stability"),
        regime_robustness=_float_or_none(robustness_row, "regime_robustness"),
        parameter_stability=_float_or_none(robustness_row, "parameter_stability"),
        stress_resilience=_float_or_none(robustness_row, "stress_resilience"),
        overfitting_resistance=_float_or_none(robustness_row, "overfitting_resistance"),
    )

    stage_results: dict = {}

    if not wf_df.empty:
        row = wf_df.iloc[-1]
        stage_results["walk_forward"] = SimpleNamespace(
            summary={
                "fold_consistency": _float_or_none(row, "fold_consistency"),
                "avg_test_sharpe": _float_or_none(row, "avg_test_sharpe"),
                "train_test_degradation": _float_or_none(row, "train_test_degradation"),
                "fold_sharpe_cv": _float_or_none(row, "fold_sharpe_cv"),
            }
        )

    if not stress_df.empty:
        n_survived = int(stress_df["survived"].sum()) if "survived" in stress_df.columns else 0
        n_total = len(stress_df)
        avg_deg = (
            float(stress_df["sharpe_degradation"].mean())
            if "sharpe_degradation" in stress_df.columns
            else 0.0
        )
        stage_results["stress_test"] = SimpleNamespace(
            summary={
                "survival_rate": n_survived / n_total if n_total > 0 else 0.0,
                "avg_sharpe_degradation": avg_deg,
            }
        )

    if not overfit_df.empty:
        row = overfit_df.iloc[-1]
        stage_results["overfitting"] = SimpleNamespace(
            summary={
                "overfitting_probability": _float_or_none(row, "overfitting_probability"),
                "resistance_score": _float_or_none(row, "resistance_score"),
                "indicators": {
                    "train_test_degradation": _float_or_none(row, "train_test_degradation"),
                    "fold_instability": _float_or_none(row, "fold_instability"),
                    "mc_instability": _float_or_none(row, "mc_instability"),
                    "regime_concentration": _float_or_none(row, "regime_concentration"),
                    "low_trade_count_flag": _bool_or_none(row, "low_trade_count_flag"),
                    "narrow_period_alpha": _float_or_none(row, "narrow_period_alpha"),
                    "parameter_fragility": _float_or_none(row, "parameter_fragility"),
                },
            }
        )

    return SimpleNamespace(
        robustness_score=rs,
        stage_results=stage_results,
        validation_run_id=str(robustness_row.get("validation_run_id", "")),
        overall_passed=bool(robustness_row.get("overall_passed", False)),
    )


def _float_or_none(row: Any, key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        import math

        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _bool_or_none(row: Any, key: str) -> bool | None:
    v = row.get(key)
    if v is None:
        return None
    try:
        import math

        if isinstance(v, float) and math.isnan(v):
            return None
        return bool(v)
    except (TypeError, ValueError):
        return None


def _print_human_summary(summary: dict) -> None:
    cs = summary.get("candidate_score", {})
    rs = summary.get("robustness_estimate", {})
    oe = summary.get("overfit_estimate", {})
    fv = summary.get("feature_vector", {})

    print(f"\n{'=' * 65}")
    print(f"Research Intelligence: {summary.get('strategy_id', 'n/a')}")
    print(f"Experiment:            {summary.get('experiment_id', 'n/a')}")
    print(f"Run ID:                {summary.get('run_id', 'n/a')}")
    print(f"{'=' * 65}")
    print(
        f"Composite Score:       {cs.get('composite_score', 0.0):.4f}  (rank={cs.get('rank', '?')})"
    )
    print(
        f"Deployability:         {cs.get('deployability_score', 0.0):.4f}  "
        f"({'DEPLOYABLE' if cs.get('is_deployable') else 'NOT DEPLOYABLE'})"
    )
    print(f"Data Completeness:     {fv.get('data_completeness', 0.0):.0%}")
    print()

    print("  [ROBUSTNESS ESTIMATE]")
    print(f"    Probability:    {rs.get('robustness_probability', 0.0):.4f}")
    print(f"    Fragility:      {rs.get('fragility_score', 0.0):.4f}")
    print(f"    Confidence:     {rs.get('confidence', 0.0):.4f}")
    print(f"    Suitability:    {rs.get('deployment_suitability', 'n/a').upper()}")
    if rs.get("primary_weakness"):
        print(f"    Primary Weakness: {rs['primary_weakness']}")
    print()

    print("  [OVERFITTING ESTIMATE]")
    print(f"    Probability:    {oe.get('overfit_probability', 0.0):.4f}")
    print(f"    Risk Band:      {oe.get('risk_band', 'n/a').upper()}")
    if oe.get("dominant_indicator"):
        print(f"    Dominant:       {oe['dominant_indicator']}")
    for w in oe.get("warning_summary", []):
        print(f"    WARN: {w}")
    print()

    components = cs.get("components", [])
    if components:
        print("  [RANKING COMPONENTS]")
        for c in components:
            print(f"    {c['name']:<30} {c['value']:.4f}  (w={c['weight']:.3f})")
        print()

    weaknesses = cs.get("weakness_flags", [])
    if weaknesses:
        print("  [WEAKNESS FLAGS]")
        for w in weaknesses:
            print(f"    * {w}")
        print()

    cluster = summary.get("cluster_id")
    if cluster:
        print(f"  Cluster: {cluster}")
        print()

    fp = summary.get("regime_fingerprint", {})
    if fp:
        print("  [REGIME FINGERPRINT]")
        print(f"    Diversity:    {fp.get('regime_diversity', 0.0):.4f}")
        print(f"    Robustness:   {fp.get('overall_robustness', 0.0):.4f}")
        print(f"    Specialist:   {fp.get('is_regime_specialist', False)}")
        if fp.get("dominant_regime_dimension"):
            print(f"    Dominant Dim: {fp['dominant_regime_dimension']}")
        print()

    print(f"  Explanation: {cs.get('explanation', '')}")
    print()


def _print_human_clusters(clusters: list[dict]) -> None:
    print(f"\n{'=' * 65}")
    print(f"Strategy Clusters ({len(clusters)} total)")
    print(f"{'=' * 65}")
    for c in clusters:
        spam_tag = " [PARAM-SPAM?]" if c.get("is_parameter_spam") else ""
        fam = f" family={c['dominant_family']}" if c.get("dominant_family") else ""
        bias = f" regime-bias={c['regime_bias']}" if c.get("regime_bias") else ""
        print(f"\n  {c['cluster_id']} (n={c['size']}){spam_tag}{fam}{bias}")
        print(f"    Representative: {c['representative']}")
        print(f"    Variance:       {c['intra_cluster_variance']:.6f}")
        print("    Members:")
        for m in c.get("members", [])[:10]:
            print(f"      {m['strategy_id']}  d={m['distance_to_centroid']:.4f}")
        if len(c.get("members", [])) > 10:
            print(f"      ... ({len(c['members']) - 10} more)")
    print()


def cmd_rank_strategies(args: argparse.Namespace) -> int:
    base_path = Path(args.data_dir)

    validation_summary = _load_validation_summary(
        base_path,
        experiment_id=args.experiment_id,
        strategy_id=args.strategy_id,
        run_id=args.run_id or "",
        dataset_version=args.dataset_version,
    )

    from autonomous_trading_platform.research.intelligence.research_intelligence_service import (
        ResearchIntelligenceRequest,
        ResearchIntelligenceService,
    )

    svc = ResearchIntelligenceService()
    request = ResearchIntelligenceRequest(
        strategy_id=args.strategy_id,
        experiment_id=args.experiment_id,
        dataset_version=args.dataset_version,
        run_id=args.run_id or "adhoc",
        validation_summary=validation_summary,
        strategy_family=getattr(args, "family", None),
        persist=not args.no_persist,
    )
    summary = svc.analyze(request)
    d = summary.as_dict()

    if args.output == "human":
        _print_human_summary(d)
    elif args.output == "json":
        print(json.dumps(d, indent=2, default=str))
    elif args.output == "yaml":
        import yaml

        print(yaml.dump(d, default_flow_style=False))
    return 0


def cmd_cluster_strategies(args: argparse.Namespace) -> int:
    base_path = Path(args.data_dir)
    strategy_ids = [s.strip() for s in args.strategy_ids.split(",") if s.strip()]
    if not strategy_ids:
        print("ERROR: no strategy IDs provided", file=sys.stderr)
        return 1

    from autonomous_trading_platform.research.intelligence.research_intelligence_service import (
        ResearchIntelligenceRequest,
        ResearchIntelligenceService,
    )

    svc = ResearchIntelligenceService()
    summaries = []
    for sid in strategy_ids:
        vs = _load_validation_summary(
            base_path,
            experiment_id=args.experiment_id,
            strategy_id=sid,
            run_id=args.run_id or "",
            dataset_version=args.dataset_version,
        )
        req = ResearchIntelligenceRequest(
            strategy_id=sid,
            experiment_id=args.experiment_id,
            dataset_version=args.dataset_version,
            run_id=args.run_id or "adhoc",
            validation_summary=vs,
            persist=not args.no_persist,
        )
        summaries.append(svc.analyze(req))

    clusters = svc.cluster_candidates(summaries)
    cluster_dicts = [c.as_dict() for c in clusters]

    if args.output == "human":
        _print_human_clusters(cluster_dicts)
    elif args.output == "json":
        print(json.dumps(cluster_dicts, indent=2, default=str))
    elif args.output == "yaml":
        import yaml

        print(yaml.dump(cluster_dicts, default_flow_style=False))
    return 0


def cmd_summarize_research_intelligence(args: argparse.Namespace) -> int:
    return cmd_rank_strategies(args)


def cmd_analyze_overfit_risk(args: argparse.Namespace) -> int:
    base_path = Path(args.data_dir)
    vs = _load_validation_summary(
        base_path,
        experiment_id=args.experiment_id,
        strategy_id=args.strategy_id,
        run_id=args.run_id or "",
        dataset_version=args.dataset_version,
    )

    from autonomous_trading_platform.research.intelligence.overfitting_estimation_service import (
        OverfittingEstimationService,
    )
    from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
        ResearchFeatureVectorBuilder,
    )

    builder = ResearchFeatureVectorBuilder()
    vector = builder.build(
        strategy_id=args.strategy_id,
        experiment_id=args.experiment_id,
        dataset_version=args.dataset_version,
        validation_summary=vs,
    )
    svc = OverfittingEstimationService()
    estimate = svc.estimate(vector)
    d = estimate.as_dict()

    if args.output == "human":
        print(f"\n{'=' * 60}")
        print(f"Overfit Risk: {args.strategy_id}")
        print(f"{'=' * 60}")
        print(f"  Probability: {d['overfit_probability']:.4f}")
        print(f"  Risk Band:   {d['risk_band'].upper()}")
        if d.get("dominant_indicator"):
            print(f"  Dominant:    {d['dominant_indicator']}")
        for w in d.get("warning_summary", []):
            print(f"  WARN: {w}")
        print("\n  [INDICATORS]")
        for k, v in d.get("indicators", {}).items():
            val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
            print(f"    {k:<35} {val_str}")
        print()
    elif args.output == "json":
        print(json.dumps(d, indent=2, default=str))
    elif args.output == "yaml":
        import yaml

        print(yaml.dump(d, default_flow_style=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Research intelligence CLI")
    sub = parser.add_subparsers(dest="command")

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--experiment-id", required=True)
        p.add_argument("--dataset-version", required=True)
        p.add_argument("--output", choices=["human", "json", "yaml"], default="human")
        p.add_argument("--data-dir", default="data")
        p.add_argument("--no-persist", action="store_true")
        p.add_argument("--run-id", default=None)

    rank_p = sub.add_parser("rank-strategies")
    _add_common(rank_p)
    rank_p.add_argument("--strategy-id", required=True)
    rank_p.add_argument("--family", default=None)

    cluster_p = sub.add_parser("cluster-strategies")
    _add_common(cluster_p)
    cluster_p.add_argument("--strategy-ids", required=True)

    summarize_p = sub.add_parser("summarize-research-intelligence")
    _add_common(summarize_p)
    summarize_p.add_argument("--strategy-id", required=True)
    summarize_p.add_argument("--family", default=None)

    overfit_p = sub.add_parser("analyze-overfit-risk")
    _add_common(overfit_p)
    overfit_p.add_argument("--strategy-id", required=True)

    args = parser.parse_args()

    commands = {
        "rank-strategies": cmd_rank_strategies,
        "cluster-strategies": cmd_cluster_strategies,
        "summarize-research-intelligence": cmd_summarize_research_intelligence,
        "analyze-overfit-risk": cmd_analyze_overfit_risk,
    }

    if args.command not in commands:
        parser.print_help()
        return 1

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
