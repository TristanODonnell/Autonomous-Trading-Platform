"""
Platform Backtest Visualization Runner

Usage:
    python -m visualization.run_all
    python -m visualization.run_all --artifact artifacts/platform/backtests/full_year_demo.json
    python -m visualization.run_all --artifact <path> --out-dir visualization/outputs/my_run
    python -m visualization.run_all --list-artifacts

Loads a PlatformBacktestArtifact JSON bundle, augments with synthetic
financial performance data if the platform hasn't executed real trades yet,
and exports all storytelling charts as high-resolution PNGs.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Default artifact to use when none is specified
# ---------------------------------------------------------------------------
_DEFAULT_ARTIFACT = Path("artifacts/platform/backtests/full_year_demo.json")
_ARTIFACTS_DIR = Path("artifacts/platform/backtests")
_DEFAULT_OUT_DIR = Path("visualization/outputs")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate platform backtest storytelling charts from artifact JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--artifact",
        "-a",
        type=Path,
        default=_DEFAULT_ARTIFACT,
        help=f"Path to artifact bundle JSON (default: {_DEFAULT_ARTIFACT})",
    )
    parser.add_argument(
        "--out-dir",
        "-o",
        type=Path,
        default=None,
        help="Output directory for PNGs (default: visualization/outputs/<fixture_name>)",
    )
    parser.add_argument(
        "--starting-cash",
        type=float,
        default=100_000.0,
        help="Starting capital (default: 100000)",
    )
    parser.add_argument(
        "--charts",
        nargs="+",
        default=None,
        help="Run only specific charts by number: 1 2 3 ... (default: all)",
    )
    parser.add_argument(
        "--list-artifacts",
        action="store_true",
        help="List available artifact files and exit",
    )
    args = parser.parse_args()

    if args.list_artifacts:
        return _list_artifacts()

    # ── Load ──────────────────────────────────────────────────────────────────
    artifact_path = args.artifact
    if not artifact_path.exists():
        print(f"[error] Artifact not found: {artifact_path}", file=sys.stderr)
        print("        Use --list-artifacts to see available files.", file=sys.stderr)
        return 1

    print(f"\n{'=' * 60}")
    print("  Platform Backtest Visualization")
    print(f"{'=' * 60}")
    print(f"  Artifact : {artifact_path}")

    from visualization.loader import load

    data = load(artifact_path)

    print(f"  Fixture  : {data.fixture_name or '(unnamed)'}")
    print(f"  Range    : {data.start_date} to {data.end_date}")
    print(f"  Ticks    : {data.ticks_ok}/{data.ticks_attempted} ok")
    print(f"  Symbols  : {', '.join(data.symbols)}")
    print(
        f"  Trading  : {'[synthetic financial data]' if data.is_synthetic else '[real fill data]'}"
    )

    # ── Augment ───────────────────────────────────────────────────────────────
    from visualization.synthetic import augment

    data = augment(data, starting_cash=args.starting_cash)

    if data.is_synthetic:
        stats = getattr(data, "synthetic_stats", {})
        print("\n  Synthetic performance (for storytelling):")
        print(
            f"    Total Return : {stats.get('total_return_pct', '—')}%  "
            f"(Benchmark: {stats.get('total_return_pct_bench', '—')}%)"
        )
        print(f"    Sharpe Ratio : {stats.get('sharpe_ratio', '—')}")
        print(
            f"    Max Drawdown : {stats.get('max_drawdown_pct', '—')}%  "
            f"({stats.get('max_dd_duration_days', '—')} days)"
        )
        print(f"    Calmar Ratio : {stats.get('calmar_ratio', '—')}")

    # ── Output directory ──────────────────────────────────────────────────────
    fixture_slug = (data.fixture_name or artifact_path.stem).replace(" ", "_")
    out_dir = args.out_dir or (_DEFAULT_OUT_DIR / fixture_slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n  Output   : {out_dir}/")

    # ── Chart registry ────────────────────────────────────────────────────────
    from visualization.charts import (
        benchmark_gauntlet,
        cost_sensitivity,
        drawdown,
        equity_curve,
        execution_quality,
        exposure_allocation,
        governance_timeline,
        monthly_returns,
        operational_health,
        performance_table,
        platform_contribution,
        research_funnel,
        rolling_risk,
        strategy_lifecycle,
    )

    chart_registry = [
        (1, "Equity Curve", equity_curve.render),
        (2, "Drawdown Analysis", drawdown.render),
        (3, "Monthly Returns", monthly_returns.render),
        (4, "Performance Table", performance_table.render),
        (5, "Governance Timeline", governance_timeline.render),
        (6, "Operational Health", operational_health.render),
        (7, "Platform Contribution", platform_contribution.render),
        (8, "Execution Quality", execution_quality.render),
        (9, "Benchmark Gauntlet", benchmark_gauntlet.render),
        (10, "Cost Sensitivity", cost_sensitivity.render),
        (11, "Rolling Risk Metrics", rolling_risk.render),
        (12, "Exposure & Allocation", exposure_allocation.render),
        (13, "Strategy Lifecycle", strategy_lifecycle.render),
        (14, "Research Funnel", research_funnel.render),
    ]

    filter_nums = set(int(x) for x in args.charts) if args.charts else None

    print("\n  Rendering charts:")
    print(f"  {'-' * 50}")

    generated: list[Path] = []
    errors: list[str] = []

    for num, name, render_fn in chart_registry:
        if filter_nums and num not in filter_nums:
            continue

        t0 = time.time()
        try:
            out_path = render_fn(data, out_dir)
            elapsed = time.time() - t0
            kb = out_path.stat().st_size / 1024
            print(f"  [{num:02d}] {name:<28}  {out_path.name}  ({kb:.0f} KB, {elapsed:.1f}s)")
            generated.append(out_path)
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"  [{num:02d}] {name:<28}  ERROR: {exc}  ({elapsed:.1f}s)")
            errors.append(f"Chart {num} ({name}): {exc}")

    # ── Companion markdown reports ────────────────────────────────────────────
    from visualization.reporting import (
        generate_chart_explanations,
        generate_methodology,
        generate_robustness_next_steps,
    )

    print("\n  Generating companion reports:")
    print(f"  {'-' * 50}")

    report_fns = [
        (
            "Methodology & Assumptions",
            lambda: generate_methodology(data, out_dir, args.starting_cash),
        ),
        ("Chart Explanations", lambda: generate_chart_explanations(data, out_dir)),
        ("Robustness Roadmap", lambda: generate_robustness_next_steps(out_dir)),
    ]

    for report_name, report_fn in report_fns:
        t0 = time.time()
        try:
            out_path = report_fn()
            elapsed = time.time() - t0
            kb = out_path.stat().st_size / 1024
            print(f"  [md] {report_name:<28}  {out_path.name}  ({kb:.0f} KB, {elapsed:.1f}s)")
            generated.append(out_path)
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"  [md] {report_name:<28}  ERROR: {exc}  ({elapsed:.1f}s)")
            errors.append(f"Report ({report_name}): {exc}")

    print(f"\n  {'=' * 50}")
    n_charts = sum(1 for p in generated if p.suffix == ".png")
    n_reports = sum(1 for p in generated if p.suffix == ".md")
    print(
        f"  Generated {n_charts}/{len(chart_registry)} charts, {n_reports}/{len(report_fns)} reports"
    )
    if errors:
        print(f"  {len(errors)} error(s):")
        for e in errors:
            print(f"    • {e}")
    print(f"  Output directory: {out_dir.resolve()}")
    print()

    return 0 if not errors else 1


def _list_artifacts() -> int:
    if not _ARTIFACTS_DIR.exists():
        print(f"Artifacts directory not found: {_ARTIFACTS_DIR}")
        return 1

    files = sorted(_ARTIFACTS_DIR.glob("*.json"))
    if not files:
        print(f"No artifact files found in {_ARTIFACTS_DIR}")
        return 0

    import json

    print(f"\nAvailable platform backtest artifacts ({len(files)} files):")
    print(f"{'─' * 70}")
    for f in files:
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            start = raw.get("start_date", "?")
            end = raw.get("end_date", "?")
            ticks = (raw.get("runtime") or {}).get("ticks_ok", "?")
            orders = (raw.get("runtime") or {}).get("total_orders", 0)
            marker = "*" if orders and orders > 0 else "~"
            print(f"  {marker} {f.name:<40} {start} to {end}  ({ticks} ticks)")
        except Exception:
            print(f"    {f.name:<40} [unreadable]")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
