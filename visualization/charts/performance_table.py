"""
Chart 4 — Performance Summary Table (pandas Styler → PNG)

Tells the story: the headline numbers at a glance.

Renders a colour-coded performance table comparing:
  Platform vs SPY Benchmark vs Excess/Alpha

Exported as PNG via matplotlib so it embeds cleanly into slides.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd

from visualization import theme
from visualization.loader import ArtifactData

# Metric display config: (row label, stat_key_platform, stat_key_bench, format, good_direction)
_METRICS = [
    ("Total Return", "total_return_pct", "total_return_pct_bench", "{:.1f}%", "high"),
    ("CAGR", "cagr_pct", "cagr_pct_bench", "{:.1f}%", "high"),
    ("Sharpe Ratio", "sharpe_ratio", "sharpe_ratio_bench", "{:.2f}", "high"),
    ("Sortino Ratio", "sortino_ratio", None, "{:.2f}", "high"),
    ("Max Drawdown", "max_drawdown_pct", "max_drawdown_pct_bench", "{:.1f}%", "low"),
    ("Max DD Duration", "max_dd_duration_days", None, "{:.0f}d", "low"),
    ("Calmar Ratio", "calmar_ratio", None, "{:.2f}", "high"),
    ("Volatility", "volatility_pct", "volatility_pct_bench", "{:.1f}%", "low"),
    ("Win Rate", "win_rate_pct", None, "{:.1f}%", "high"),
    ("Information Ratio", "information_ratio", None, "{:.2f}", "high"),
    ("Tracking Error", "tracking_error_pct", None, "{:.1f}%", None),
    ("Excess Return", "excess_return_pct", None, "{:+.1f}%", "high"),
    ("Final Value", "final_value", "final_value_bench", "${:,.0f}", "high"),
    ("Profit / Loss", "profit_loss_usd", None, "${:+,.0f}", "high"),
]


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    stats = getattr(data, "synthetic_stats", {})
    starting_cash = getattr(data, "starting_cash", 100_000.0)

    rows = []
    for label, key_p, key_b, fmt, direction in _METRICS:
        val_p = stats.get(key_p)
        val_b = stats.get(key_b) if key_b else None

        str_p = fmt.format(val_p) if val_p is not None else "—"
        str_b = fmt.format(val_b) if val_b is not None else "—"

        # Excess vs benchmark
        if val_p is not None and val_b is not None:
            excess = val_p - val_b
            sign = "+" if excess >= 0 else ""
            str_excess = f"{sign}{excess:.2f}" if isinstance(val_p, float) else "—"
        else:
            str_excess = "—"

        rows.append(
            {
                "Metric": label,
                "Platform": str_p,
                "Benchmark": str_b,
                "vs. Bench": str_excess,
                "_val_p": val_p,
                "_val_b": val_b,
                "_direction": direction,
            }
        )

    df_display = pd.DataFrame(rows)

    # ── Render as matplotlib table ────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(13, 9))
    fig.patch.set_facecolor(theme.BG)
    ax.set_facecolor(theme.BG)
    ax.axis("off")

    display_cols = ["Metric", "Platform", "Benchmark", "vs. Bench"]
    cell_data = df_display[display_cols].values.tolist()
    col_labels = display_cols
    col_widths = [0.32, 0.22, 0.24, 0.22]

    table = ax.table(
        cellText=cell_data,
        colLabels=col_labels,
        cellLoc="center",
        loc="center",
        colWidths=col_widths,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10.5)

    # Style header
    for j, _col in enumerate(col_labels):
        cell = table[0, j]
        cell.set_facecolor(theme.SURFACE2)
        cell.set_text_props(color=theme.TEXT, fontsize=11, fontweight="bold")
        cell.set_edgecolor(theme.BORDER)

    # Style data rows
    for i, row in enumerate(rows, start=1):
        direction = row["_direction"]
        val_p = row["_val_p"]
        val_b = row["_val_b"]
        row_bg = theme.SURFACE if i % 2 == 0 else theme.SURFACE2

        for j in range(len(col_labels)):
            cell = table[i, j]
            cell.set_facecolor(row_bg)
            cell.set_edgecolor(theme.BORDER)

            col_name = col_labels[j]
            txt_color = theme.TEXT

            if col_name == "Platform" and val_p is not None and direction:
                txt_color = _value_color(float(val_p), direction)
            elif col_name == "vs. Bench" and val_p is not None and val_b is not None:
                excess = float(val_p) - float(val_b)
                txt_color = theme.ACCENT if excess >= 0 else theme.RED
            elif col_name == "Metric":
                txt_color = theme.TEXT2

            cell.set_text_props(color=txt_color, fontsize=10.5)
            cell.set_height(0.064)

    # Scale the table to fill the axes
    table.scale(1, 1.4)

    ax.set_title(
        "Performance Summary",
        color=theme.TEXT,
        fontsize=15,
        pad=20,
        loc="left",
    )
    ax.text(
        0.0,
        1.01,
        f"{data.start_date} → {data.end_date}  ·  "
        f"Starting capital ${starting_cash:,.0f}  ·  "
        f"{'[synthetic financial data]' if data.is_synthetic else '[live data]'}",
        transform=ax.transAxes,
        fontsize=9,
        color=theme.TEXT2,
        va="bottom",
    )

    # Legend for colours
    handles = [
        mpatches.Patch(color=theme.ACCENT, label="Above target / positive"),
        mpatches.Patch(color=theme.RED, label="Below target / negative"),
        mpatches.Patch(color=theme.YELLOW, label="Moderate / borderline"),
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=8, framealpha=0.7)

    theme.watermark(fig)
    plt.tight_layout()

    out = out_dir / "04_performance_table.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def _value_color(val: float, direction: str) -> str:
    """Return accent/yellow/red based on value quality."""
    if direction == "high":
        if val >= 0:
            return theme.ACCENT
        return theme.RED
    if direction == "low":
        if val <= 0:
            return theme.ACCENT
        if abs(val) < 20:
            return theme.YELLOW
        return theme.RED
    return theme.TEXT
