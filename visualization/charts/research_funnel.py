"""
Chart 14 — Research Pipeline Funnel

Tells the story: what happened to the 100+ strategy candidates generated each month?

Shows:
  - Monthly funnel: total runs → stage 1 → stage 2 → stage 3 → deployable
  - Pass-rate at each stage across all research runs
  - Top composite score and regime diversity over time
  - Cumulative deployable strategies seeded to governance
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from visualization import theme
from visualization.loader import ArtifactData


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df

    # Pull only ticks where research fired
    res_df = df[df["research_fired"]].copy()

    fig = plt.figure(figsize=(18, 13))
    fig.patch.set_facecolor(theme.BG)

    gs = fig.add_gridspec(
        3,
        2,
        height_ratios=[1.8, 1.0, 1.0],
        width_ratios=[2.5, 1],
        hspace=0.42,
        wspace=0.28,
    )

    ax_funnel = fig.add_subplot(gs[0, :])  # monthly stacked funnel bars (full width)
    ax_score = fig.add_subplot(gs[1, 0])  # top composite score over time
    ax_regime = fig.add_subplot(gs[1, 1])  # regime diversity score
    ax_cumdepl = fig.add_subplot(gs[2, 0])  # cumulative deployable seeded
    ax_summary = fig.add_subplot(gs[2, 1])  # overall funnel summary text

    # ── Monthly funnel bars ────────────────────────────────────────────────────
    if res_df.empty:
        ax_funnel.text(
            0.5,
            0.5,
            "No research ticks recorded",
            ha="center",
            va="center",
            transform=ax_funnel.transAxes,
            color=theme.TEXT2,
            fontsize=12,
        )
    else:
        x = np.arange(len(res_df))
        bar_w = 0.16

        stages = [
            ("research_total_runs", "Total runs", theme.BLUE, 0),
            ("research_stage1", "Stage 1 pass", theme.PURPLE, 1),
            ("research_stage2", "Stage 2 pass", theme.ACCENT, 2),
            ("research_stage3", "Stage 3 pass", theme.YELLOW, 3),
            ("research_deployable", "Deployable", "#FF9F43", 4),
        ]

        for col, label, color, offset in stages:
            vals = res_df[col].fillna(0).values
            bars = ax_funnel.bar(
                x + (offset - 2) * bar_w,
                vals,
                bar_w,
                color=color,
                label=label,
                zorder=3,
                alpha=0.85,
            )
            # Value labels on bars (skip 0s to keep it clean)
            for bar, v in zip(bars, vals, strict=False):
                if v > 0:
                    ax_funnel.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.3,
                        str(int(v)),
                        ha="center",
                        va="bottom",
                        fontsize=7,
                        color=color,
                    )

        ax_funnel.set_xticks(x)
        ax_funnel.set_xticklabels(
            [d.strftime("%b '%y") for d in res_df.index],
            rotation=30,
            ha="right",
            fontsize=9,
        )
        ax_funnel.set_title(
            "Monthly Research Pipeline Funnel  (candidates per stage)", color=theme.TEXT, pad=10
        )
        ax_funnel.set_ylabel("Strategy Count", color=theme.TEXT)
        ax_funnel.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        ax_funnel.legend(fontsize=9, framealpha=0.6, loc="upper right")

        # Annotate if all pass-rates are 0 at stage 1
        all_zero_s1 = (res_df["research_stage1"].fillna(0) == 0).all()
        if all_zero_s1:
            ax_funnel.text(
                0.5,
                0.65,
                "0 candidates passed Stage 1 in any month.\n"
                "Research is running but filter thresholds may be too strict\n"
                "or simulation metrics are not populating correctly.",
                transform=ax_funnel.transAxes,
                ha="center",
                va="center",
                fontsize=9.5,
                color=theme.YELLOW,
                bbox=dict(
                    facecolor=theme.SURFACE2,
                    edgecolor=theme.YELLOW,
                    alpha=0.88,
                    boxstyle="round,pad=0.5",
                ),
            )

    # ── Top composite score over time ──────────────────────────────────────────
    if not res_df.empty:
        scores = res_df["research_top_score"].fillna(0)
        ax_score.plot(
            res_df.index, scores, color=theme.ACCENT, marker="o", markersize=5, linewidth=1.6
        )
        ax_score.fill_between(res_df.index, scores, alpha=0.12, color=theme.ACCENT)
        ax_score.axhline(0, color=theme.BORDER, linewidth=0.7, linestyle="--")
        if scores.max() == 0:
            ax_score.set_ylim(-0.1, 1.0)
            ax_score.text(
                0.5,
                0.5,
                "no qualifying strategies",
                transform=ax_score.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                color=theme.TEXT2,
            )
    else:
        ax_score.text(
            0.5,
            0.5,
            "no data",
            transform=ax_score.transAxes,
            ha="center",
            va="center",
            color=theme.TEXT2,
        )

    ax_score.set_title("Top Composite Score (per research run)", color=theme.TEXT, pad=8)
    ax_score.set_ylabel("Score", color=theme.TEXT)
    ax_score.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_score.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax_score.get_xticklabels(), rotation=30, ha="right", fontsize=8)

    # ── Regime diversity score ─────────────────────────────────────────────────
    if not res_df.empty:
        diversity = res_df["research_regime_diversity"].fillna(0)
        ax_regime.bar(range(len(res_df)), diversity, color=theme.PURPLE, alpha=0.75, zorder=3)
        ax_regime.set_xticks(range(len(res_df)))
        ax_regime.set_xticklabels(
            [d.strftime("%b") for d in res_df.index],
            rotation=30,
            ha="right",
            fontsize=8,
        )
        if diversity.max() == 0:
            ax_regime.set_ylim(0, 1)
            ax_regime.text(
                0.5,
                0.5,
                "0.0 all months",
                transform=ax_regime.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                color=theme.TEXT2,
            )
    else:
        ax_regime.text(
            0.5,
            0.5,
            "no data",
            transform=ax_regime.transAxes,
            ha="center",
            va="center",
            color=theme.TEXT2,
        )

    ax_regime.set_title("Regime Diversity Score", color=theme.TEXT, pad=8, fontsize=10)
    ax_regime.set_ylabel("Score", color=theme.TEXT, fontsize=9)

    # ── Cumulative deployable seeded ───────────────────────────────────────────
    if not res_df.empty:
        cum_deployable = res_df["research_deployable"].fillna(0).cumsum()
        ax_cumdepl.step(res_df.index, cum_deployable, where="post", color="#FF9F43", linewidth=2.0)
        ax_cumdepl.fill_between(
            res_df.index, cum_deployable, step="post", alpha=0.15, color="#FF9F43"
        )
        ax_cumdepl.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        if cum_deployable.max() == 0:
            ax_cumdepl.set_ylim(0, 1)
            ax_cumdepl.text(
                0.5,
                0.5,
                "0 strategies seeded to governance",
                transform=ax_cumdepl.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                color=theme.YELLOW,
            )
    else:
        ax_cumdepl.text(
            0.5,
            0.5,
            "no data",
            transform=ax_cumdepl.transAxes,
            ha="center",
            va="center",
            color=theme.TEXT2,
        )

    ax_cumdepl.set_title("Cumulative Strategies Seeded to Governance", color=theme.TEXT, pad=8)
    ax_cumdepl.set_ylabel("Count", color=theme.TEXT)
    ax_cumdepl.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_cumdepl.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax_cumdepl.get_xticklabels(), rotation=30, ha="right", fontsize=8)

    # ── Overall funnel summary ─────────────────────────────────────────────────
    ax_summary.set_facecolor(theme.SURFACE)
    ax_summary.axis("off")

    if not res_df.empty:
        total_runs = int(res_df["research_total_runs"].sum())
        total_s1 = int(res_df["research_stage1"].sum())
        total_s2 = int(res_df["research_stage2"].sum())
        total_s3 = int(res_df["research_stage3"].sum())
        total_depl = int(res_df["research_deployable"].sum())
        n_months = len(res_df)

        def pct(num, denom):
            return f"{100 * num / denom:.1f}%" if denom > 0 else "—"

        lines = [
            ("Research runs", f"{n_months} months"),
            ("Total candidates", f"{total_runs}"),
            ("→ Stage 1 pass", f"{total_s1}  ({pct(total_s1, total_runs)})"),
            ("→ Stage 2 pass", f"{total_s2}  ({pct(total_s2, total_s1 or 1)})"),
            ("→ Stage 3 pass", f"{total_s3}  ({pct(total_s3, total_s2 or 1)})"),
            ("→ Deployable", f"{total_depl}  ({pct(total_depl, total_runs)})"),
        ]

        colors = [theme.TEXT2, theme.TEXT, theme.BLUE, theme.PURPLE, theme.ACCENT, "#FF9F43"]

        for i, ((label, value), color) in enumerate(zip(lines, colors, strict=False)):
            y = 0.88 - i * 0.14
            ax_summary.text(
                0.05,
                y,
                label,
                transform=ax_summary.transAxes,
                fontsize=9.5,
                color=theme.TEXT2,
                va="top",
            )
            ax_summary.text(
                0.95,
                y,
                value,
                transform=ax_summary.transAxes,
                fontsize=9.5,
                color=color,
                va="top",
                ha="right",
                fontweight="bold",
            )

    ax_summary.set_title("Funnel Summary", color=theme.TEXT, pad=8, fontsize=10)

    fig.suptitle(
        f"Research Pipeline  |  {data.start_date} → {data.end_date}  ·  "
        f"{'[synthetic equity]' if data.is_synthetic else '[live]'}",
        color=theme.TEXT,
        fontsize=13,
        y=0.995,
    )
    theme.watermark(fig)

    out = out_dir / "14_research_funnel.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
