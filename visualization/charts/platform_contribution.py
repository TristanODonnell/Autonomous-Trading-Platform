"""
Chart 7 — Platform Contribution Summary

Tells the story: which systems drove (or cost) performance?

Shows:
  - Horizontal bar chart: contribution score per platform domain
  - Strategy catalog breakdown (paper vs live, by type)
  - Research funnel (runs → passed filters)
  - Execution quality summary (fills, adverse rate)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from visualization import theme
from visualization.loader import ArtifactData


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    stats = getattr(data, "synthetic_stats", {})
    gov = data.governance_summary
    research = data.research_summary
    catalog = data.strategy_catalog
    exec_s = data.execution_summary

    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor(theme.BG)
    gs = fig.add_gridspec(2, 3, hspace=0.5, wspace=0.4)

    ax_contrib = fig.add_subplot(gs[:, 0])  # full left column
    ax_catalog = fig.add_subplot(gs[0, 1])
    ax_funnel = fig.add_subplot(gs[0, 2])
    ax_exec = fig.add_subplot(gs[1, 1])
    ax_risk = fig.add_subplot(gs[1, 2])

    # ── Domain contribution bar chart ─────────────────────────────────────────
    # Score each domain based on what the artifact tells us
    # These are directional estimates, not exact attribution
    excess_return = stats.get("excess_return_pct", 0) or 0
    sharpe = stats.get("sharpe_ratio", 1.0) or 1.0
    max_dd = abs(stats.get("max_drawdown_pct", 10.0) or 10.0)
    adverse_rate = exec_s.get("adverse_fills", 0) / max(exec_s.get("fills_this_cycle", 1), 1)

    scores = {
        "Research": min(
            research.get("passed_filters", 0) / max(research.get("total_runs", 1), 1) * 100, 100
        )
        * 0.3,
        "Governance": max(
            0, (gov.get("promotions_executed", 0) * 5 - gov.get("demotions_executed", 0) * 2)
        ),
        "Allocation": max(0, excess_return * 0.4),
        "Risk Controls": max(0, (20 - max_dd) * 0.5),
        "Portfolio Constr.": max(0, (sharpe - 1.0) * 10),
        "Execution": max(0, (1 - adverse_rate) * 10 - 2),
    }

    domain_list = list(scores.keys())
    score_list = [scores[d] for d in domain_list]
    colors = [theme.ACCENT if s >= 0 else theme.RED for s in score_list]
    # Sort by score descending
    order = sorted(range(len(score_list)), key=lambda i: score_list[i])
    domain_ordered = [domain_list[i] for i in order]
    score_ordered = [score_list[i] for i in order]
    color_ordered = [colors[i] for i in order]

    bars = ax_contrib.barh(
        domain_ordered,
        score_ordered,
        color=color_ordered,
        height=0.55,
        edgecolor=theme.BORDER,
    )
    ax_contrib.axvline(0, color=theme.TEXT2, linewidth=0.8)

    for bar, val in zip(bars, score_ordered, strict=False):
        x = val + 0.3 if val >= 0 else val - 0.3
        ha = "left" if val >= 0 else "right"
        ax_contrib.text(
            x,
            bar.get_y() + bar.get_height() / 2,
            f"{val:.1f}",
            va="center",
            ha=ha,
            fontsize=9,
            color=theme.TEXT,
        )

    ax_contrib.set_title(
        "Estimated Platform Contribution\n(directional estimate — not exact attribution)",
        color=theme.TEXT,
        pad=12,
    )
    ax_contrib.set_xlabel("Contribution Score", color=theme.TEXT2, fontsize=9)
    ax_contrib.tick_params(axis="y", labelsize=9.5)

    note = (
        "DIRECTIONAL ESTIMATE ONLY.\n"
        "Scores derived from artifact metadata:\n"
        "research pass rate, governance actions,\n"
        "Sharpe, max drawdown, adverse fills.\n"
        "Exact attribution requires strategy-level\n"
        "P&L, allocation history, and per-fill data."
    )
    ax_contrib.text(
        0.97,
        0.02,
        note,
        transform=ax_contrib.transAxes,
        fontsize=7,
        color=theme.TEXT2,
        va="bottom",
        ha="right",
        style="italic",
    )

    # ── Strategy catalog donut ────────────────────────────────────────────────
    paper = catalog.get("paper_count", 0)
    live = catalog.get("live_count", 0)
    total_active = catalog.get("total_active", max(paper + live, 1))

    ax_catalog.pie(
        [max(paper, 0.001), max(live, 0.001)],
        labels=[f"Paper ({paper})", f"Live ({live})"],
        colors=[theme.BLUE, theme.ACCENT],
        autopct="%1.0f%%",
        pctdistance=0.65,
        startangle=90,
        wedgeprops=dict(width=0.45, edgecolor=theme.BG),
        textprops={"color": theme.TEXT, "fontsize": 9},
    )
    ax_catalog.set_title(
        f"Strategy Catalog\n{total_active} active",
        color=theme.TEXT,
        pad=8,
        fontsize=11,
    )

    # ── Research funnel ───────────────────────────────────────────────────────
    total_runs = research.get("total_runs", 0) or 0
    passed = research.get("passed_filters", 0) or 0
    pass_rate = passed / max(total_runs, 1) * 100

    funnel_vals = [total_runs, passed]
    funnel_labels = [f"Total Runs\n{total_runs}", f"Passed Filters\n{passed}"]
    funnel_colors = [theme.BLUE, theme.ACCENT]

    bars_f = ax_funnel.barh(
        funnel_labels, funnel_vals, color=funnel_colors, height=0.4, edgecolor=theme.BORDER
    )
    ax_funnel.set_title(
        f"Research Funnel\n{pass_rate:.0f}% pass rate",
        color=theme.TEXT,
        pad=8,
        fontsize=11,
    )
    ax_funnel.tick_params(axis="y", labelsize=9)
    for bar, val in zip(bars_f, funnel_vals, strict=False):
        ax_funnel.text(
            val + max(total_runs * 0.02, 1),
            bar.get_y() + bar.get_height() / 2,
            str(val),
            va="center",
            fontsize=9,
            color=theme.TEXT,
        )
    ax_funnel.set_xlim(0, total_runs * 1.18)
    ax_funnel.set_xlabel("Strategy Count", fontsize=9, color=theme.TEXT2)

    # ── Execution summary ─────────────────────────────────────────────────────
    exec_metrics = {
        "Total Fills": data.total_fills,
        "Adverse Fills": exec_s.get("adverse_fills", 0) or 0,
        "Open Orders": exec_s.get("open_order_count", 0) or 0,
    }

    # Also pull synthetic execution data if available
    df = data.tick_df
    avg_slip = df["avg_slippage_bps_s"].mean() if "avg_slippage_bps_s" in df.columns else None

    metric_names = list(exec_metrics.keys())
    metric_vals = list(exec_metrics.values())
    bar_colors = [
        theme.ACCENT,
        theme.RED if exec_metrics["Adverse Fills"] > 0 else theme.ACCENT,
        theme.BLUE,
    ]

    ax_exec.bar(
        metric_names,
        metric_vals,
        color=bar_colors[: len(metric_names)],
        edgecolor=theme.BORDER,
        width=0.5,
    )
    for i, val in enumerate(metric_vals):
        ax_exec.text(
            i,
            val + max(max(metric_vals) * 0.02, 0.1),
            str(val),
            ha="center",
            fontsize=9,
            color=theme.TEXT,
        )

    if avg_slip is not None:
        ax_exec.set_title(
            f"Execution Summary\nAvg Slippage: {avg_slip:.1f} bps (synthetic)",
            color=theme.TEXT,
            pad=8,
            fontsize=11,
        )
    else:
        ax_exec.set_title("Execution Summary", color=theme.TEXT, pad=8, fontsize=11)

    ax_exec.tick_params(axis="x", labelsize=8.5)

    # ── Risk summary bars ─────────────────────────────────────────────────────
    risk = data.risk_summary
    ladder_states = risk.get("drawdown_ladder_states") or {}
    state_counts: dict[str, int] = {}
    for _strat_id, state in ladder_states.items():
        state_counts[state] = state_counts.get(state, 0) + 1

    if state_counts:
        states = list(state_counts.keys())
        counts = list(state_counts.values())
        rcolors = [theme.LADDER_COLORS.get(s.lower(), theme.TEXT2) for s in states]

        ax_risk.bar(states, counts, color=rcolors, edgecolor=theme.BORDER, width=0.5)
        ax_risk.set_title(
            "Drawdown Ladder States\n(end of run)", color=theme.TEXT, pad=8, fontsize=11
        )
        ax_risk.tick_params(axis="x", labelsize=8.5, rotation=20)
        for i, val in enumerate(counts):
            ax_risk.text(i, val + 0.05, str(val), ha="center", fontsize=9, color=theme.TEXT)
    else:
        ax_risk.set_title("Drawdown Ladder States\n(no data)", color=theme.TEXT, pad=8, fontsize=11)
        ax_risk.text(
            0.5,
            0.5,
            "No ladder data",
            ha="center",
            va="center",
            transform=ax_risk.transAxes,
            color=theme.TEXT2,
            fontsize=10,
        )

    ax_risk.set_ylabel("Strategy Count", fontsize=9, color=theme.TEXT2)

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = out_dir / "07_platform_contribution.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
