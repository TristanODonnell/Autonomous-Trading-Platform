"""
Chart 3 — Monthly Returns Heatmap + Distribution

Tells the story: was performance consistent across the year?

Shows:
  - Seaborn heatmap: months × years, cell = monthly return %
  - Colour scale: green (positive) → red (negative)
  - Annual summary column
  - Return distribution (histogram + KDE)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from visualization import theme
from visualization.loader import ArtifactData

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df
    daily_returns = df["daily_return_s"].dropna()

    # ── Build monthly returns table ───────────────────────────────────────────
    monthly = _build_monthly_table(daily_returns)

    fig = plt.figure(figsize=(18, 8))
    fig.patch.set_facecolor(theme.BG)
    gs = fig.add_gridspec(1, 2, width_ratios=[3, 1], wspace=0.06)
    ax_heat = fig.add_subplot(gs[0])
    ax_dist = fig.add_subplot(gs[1])

    # ── Heatmap ───────────────────────────────────────────────────────────────
    # Add "Annual" column
    annual_col = monthly.sum(axis=1).rename("Annual")
    heatmap_data = pd.concat([monthly, annual_col], axis=1)

    col_labels = _MONTH_ABBR[: monthly.shape[1]] + ["Annual"]
    heatmap_data.columns = col_labels

    vmax = max(
        abs(heatmap_data.values[~np.isnan(heatmap_data.values)].max()),
        abs(heatmap_data.values[~np.isnan(heatmap_data.values)].min()),
        0.03,
    )

    cmap = sns.diverging_palette(
        10,
        145,  # red → green
        s=85,
        l=45,
        sep=10,
        as_cmap=True,
    )

    sns.heatmap(
        heatmap_data,
        ax=ax_heat,
        annot=True,
        fmt=".1f",
        cmap=cmap,
        center=0,
        vmin=-vmax,
        vmax=vmax,
        linewidths=0.4,
        linecolor=theme.BORDER,
        cbar=False,
        annot_kws={"size": 9, "color": theme.TEXT},
    )

    # Highlight the Annual column border
    for j in range(len(heatmap_data)):
        ax_heat.add_patch(
            plt.Rectangle(
                (heatmap_data.shape[1] - 1, j),
                1,
                1,
                fill=False,
                edgecolor=theme.BORDER,
                lw=1.5,
            )
        )

    ax_heat.set_title("Monthly Returns (%)", color=theme.TEXT, pad=12, fontsize=14)
    ax_heat.set_xlabel("")
    ax_heat.set_ylabel("")
    ax_heat.tick_params(axis="both", colors=theme.TEXT2)
    ax_heat.set_xticklabels(col_labels, rotation=0, ha="center", fontsize=9)
    ax_heat.set_yticklabels(ax_heat.get_yticklabels(), rotation=0, fontsize=9)
    theme.subtitle(
        ax_heat,
        f"{data.start_date} → {data.end_date}  ·  "
        f"{'[synthetic]' if data.is_synthetic else '[live]'}",
    )

    # ── Return distribution ───────────────────────────────────────────────────
    daily_pct = daily_returns * 100

    sns.histplot(
        daily_pct,
        ax=ax_dist,
        bins=38,
        kde=True,
        color=theme.ACCENT,
        alpha=0.55,
        line_kws={"linewidth": 1.8, "color": theme.ACCENT},
        edgecolor=theme.BORDER,
    )

    mean_r = daily_pct.mean()
    std_r = daily_pct.std()

    ax_dist.axvline(0, color=theme.TEXT2, linewidth=0.9, linestyle="--", alpha=0.6)
    ax_dist.axvline(
        mean_r, color=theme.YELLOW, linewidth=1.4, linestyle="-", label=f"Mean {mean_r:+.2f}%"
    )
    ax_dist.axvspan(mean_r - std_r, mean_r + std_r, alpha=0.08, color=theme.ACCENT)

    # Annotate negative tail
    neg_pct = (daily_pct < 0).mean() * 100
    ax_dist.text(
        0.05,
        0.95,
        f"Win days: {100 - neg_pct:.1f}%\nLoss days: {neg_pct:.1f}%",
        transform=ax_dist.transAxes,
        fontsize=8.5,
        color=theme.TEXT,
        va="top",
        bbox=dict(facecolor=theme.SURFACE2, edgecolor=theme.BORDER, boxstyle="round,pad=0.4"),
    )

    ax_dist.set_title("Daily Return Distribution", color=theme.TEXT, pad=12, fontsize=13)
    ax_dist.set_xlabel("Daily Return (%)", color=theme.TEXT2)
    ax_dist.set_ylabel("Count", color=theme.TEXT2)
    ax_dist.legend(fontsize=8)

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = out_dir / "03_monthly_returns.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def _build_monthly_table(daily_returns: pd.Series) -> pd.DataFrame:
    """Compound daily returns into monthly returns (%)."""
    monthly = (1 + daily_returns).resample("ME").prod() - 1
    monthly = monthly * 100

    result = monthly.to_frame("ret")
    result["year"] = result.index.year
    result["month"] = result.index.month

    pivot = result.pivot(index="year", columns="month", values="ret")
    pivot.columns = pd.Index([int(c) for c in pivot.columns])
    return pivot
