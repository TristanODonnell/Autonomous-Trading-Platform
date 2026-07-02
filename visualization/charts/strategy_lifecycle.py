"""
Chart 13 — Strategy Lifecycle & Governance Activity

Tells the story: are strategies actually moving through the pipeline?

Shows:
  - Cumulative promotions and demotions over the year
  - Monthly governance events (promotions / demotions / health transitions)
  - Strategies evaluated vs strategies in breach per tick
  - End-of-run strategy catalog breakdown (paper / live / total)
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
    catalog = data.strategy_catalog
    gov_summary = data.governance_summary

    total_promos = int(gov_summary.get("promotions_executed", 0) or 0)
    total_demos = int(gov_summary.get("demotions_executed", 0) or 0)
    total_health = int(gov_summary.get("health_transitions", 0) or 0)
    total_active = int(catalog.get("total_active", 0) or 0)
    paper_count = int(catalog.get("paper_count", 0) or 0)
    live_count = int(catalog.get("live_count", 0) or 0)

    fig = plt.figure(figsize=(18, 13))
    fig.patch.set_facecolor(theme.BG)

    gs = fig.add_gridspec(
        3,
        2,
        height_ratios=[1.6, 1.2, 1.0],
        width_ratios=[2.5, 1],
        hspace=0.40,
        wspace=0.25,
    )

    ax_cum = fig.add_subplot(gs[0, 0])  # cumulative promotions/demotions
    ax_catalog = fig.add_subplot(gs[0, 1])  # end-of-run catalog donut
    ax_monthly = fig.add_subplot(gs[1, 0])  # monthly governance bars
    ax_health = fig.add_subplot(gs[1, 1])  # health transitions bar
    ax_breach = fig.add_subplot(gs[2, :])  # strategies evaluated vs in breach

    # ── Cumulative promotions / demotions ──────────────────────────────────────
    cum_promos = df["promotions"].cumsum()
    cum_demos = df["demotions"].cumsum()

    ax_cum.plot(
        df.index,
        cum_promos,
        color=theme.ACCENT,
        linewidth=2.0,
        label=f"Cumulative promotions ({total_promos})",
    )
    ax_cum.plot(
        df.index,
        cum_demos,
        color=theme.RED,
        linewidth=2.0,
        label=f"Cumulative demotions ({total_demos})",
    )
    ax_cum.fill_between(df.index, cum_promos, alpha=0.10, color=theme.ACCENT)
    ax_cum.fill_between(df.index, cum_demos, alpha=0.10, color=theme.RED)

    if total_promos == 0 and total_demos == 0:
        ax_cum.text(
            0.5,
            0.5,
            "No governance transitions fired this run.\n"
            "Strategies were evaluated but never promoted or demoted.",
            transform=ax_cum.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color=theme.YELLOW,
            bbox=dict(
                facecolor=theme.SURFACE2,
                edgecolor=theme.YELLOW,
                alpha=0.85,
                boxstyle="round,pad=0.5",
            ),
        )

    ax_cum.set_title("Cumulative Governance Transitions", color=theme.TEXT, pad=8)
    ax_cum.set_ylabel("Count", color=theme.TEXT)
    ax_cum.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_cum.legend(fontsize=9, framealpha=0.6)
    ax_cum.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_cum.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax_cum.get_xticklabels(), rotation=30, ha="right", fontsize=8)

    # ── End-of-run catalog donut ───────────────────────────────────────────────
    ax_catalog.set_facecolor(theme.SURFACE)
    donut_vals = [paper_count, live_count]
    donut_colors = [theme.BLUE, theme.ACCENT]
    donut_labels = [f"Paper ({paper_count})", f"Live ({live_count})"]

    if sum(donut_vals) > 0:
        wedges, texts = ax_catalog.pie(
            donut_vals,
            colors=donut_colors,
            startangle=90,
            wedgeprops=dict(width=0.55, edgecolor=theme.BG),
        )
        ax_catalog.legend(
            wedges,
            donut_labels,
            loc="lower center",
            fontsize=8,
            framealpha=0.5,
            bbox_to_anchor=(0.5, -0.12),
        )
    else:
        ax_catalog.text(
            0.5,
            0.5,
            "No strategies\nin catalog",
            ha="center",
            va="center",
            color=theme.TEXT2,
            transform=ax_catalog.transAxes,
            fontsize=10,
        )

    ax_catalog.set_title(
        f"Strategy Catalog\n{total_active} active",
        color=theme.TEXT,
        pad=6,
        fontsize=10,
    )

    # ── Monthly governance bars ────────────────────────────────────────────────
    monthly = df[["promotions", "demotions", "health_transitions"]].resample("ME").sum()
    x = np.arange(len(monthly))
    bar_w = 0.26

    ax_monthly.bar(
        x - bar_w, monthly["promotions"], bar_w, color=theme.ACCENT, label="Promotions", zorder=3
    )
    ax_monthly.bar(x, monthly["demotions"], bar_w, color=theme.RED, label="Demotions", zorder=3)
    ax_monthly.bar(
        x + bar_w,
        monthly["health_transitions"],
        bar_w,
        color=theme.YELLOW,
        label="Health transitions",
        zorder=3,
    )

    ax_monthly.set_xticks(x)
    ax_monthly.set_xticklabels(
        [d.strftime("%b '%y") for d in monthly.index],
        rotation=35,
        ha="right",
        fontsize=8,
    )
    ax_monthly.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_monthly.set_title("Monthly Governance Events", color=theme.TEXT, pad=8)
    ax_monthly.set_ylabel("Count", color=theme.TEXT)
    ax_monthly.legend(fontsize=8, framealpha=0.6)

    if total_promos + total_demos + total_health == 0:
        ax_monthly.set_ylim(0, 1)
        ax_monthly.text(
            0.5,
            0.6,
            "0 events all year",
            transform=ax_monthly.transAxes,
            ha="center",
            va="center",
            fontsize=11,
            color=theme.YELLOW,
            alpha=0.85,
        )

    # ── Health transitions summary bar ─────────────────────────────────────────
    summary_labels = ["Promotions", "Demotions", "Health\nTransitions"]
    summary_vals = [total_promos, total_demos, total_health]
    summary_colors = [theme.ACCENT, theme.RED, theme.YELLOW]

    bars = ax_health.barh(summary_labels, summary_vals, color=summary_colors, height=0.5)
    for bar, val in zip(bars, summary_vals, strict=False):
        ax_health.text(
            max(val + 0.05, 0.1),
            bar.get_y() + bar.get_height() / 2,
            str(val),
            va="center",
            ha="left",
            fontsize=10,
            color=theme.TEXT,
        )
    ax_health.set_xlim(0, max(max(summary_vals) * 1.35, 1))
    ax_health.set_title("Year Totals", color=theme.TEXT, pad=8, fontsize=10)
    ax_health.xaxis.set_major_locator(plt.MaxNLocator(integer=True))

    # ── Strategies evaluated vs in breach ─────────────────────────────────────
    ax_breach.fill_between(
        df.index,
        df["strategies_evaluated"].fillna(0),
        alpha=0.15,
        color=theme.BLUE,
        label="Strategies evaluated",
        step="mid",
    )
    ax_breach.step(
        df.index, df["strategies_evaluated"].fillna(0), color=theme.BLUE, linewidth=1.2, where="mid"
    )

    breach_counts = df["strategies_in_breach_count"].fillna(0)
    ax_breach.fill_between(
        df.index, breach_counts, 0, color=theme.RED, alpha=0.45, step="mid", label="In breach"
    )
    ax_breach.step(df.index, breach_counts, color=theme.RED, linewidth=1.0, where="mid")

    ax_breach.set_title("Strategies Evaluated vs In Breach (daily)", color=theme.TEXT, pad=8)
    ax_breach.set_ylabel("Count", color=theme.TEXT)
    ax_breach.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_breach.legend(fontsize=9, framealpha=0.6)
    ax_breach.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_breach.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax_breach.get_xticklabels(), rotation=30, ha="right", fontsize=8)

    fig.suptitle(
        f"Strategy Lifecycle & Governance  |  "
        f"{data.start_date} → {data.end_date}  ·  "
        f"{'[synthetic equity]' if data.is_synthetic else '[live]'}",
        color=theme.TEXT,
        fontsize=13,
        y=0.99,
    )
    theme.watermark(fig)

    out = out_dir / "13_strategy_lifecycle.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
