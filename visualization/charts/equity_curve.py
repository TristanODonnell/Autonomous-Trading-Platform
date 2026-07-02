"""
Chart 1 — Equity Curve vs Benchmark

Tells the story: did the platform grow capital, and did it beat the market?

Shows:
  - Platform equity curve (green)
  - SPY benchmark curve (blue)
  - Risk-free / cash line (gray dashed)
  - Annotated timeline events from the artifact
  - Shaded drawdown episodes
  - Right-axis: cumulative return %
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import numpy as np
import pandas as pd

from visualization import theme
from visualization.loader import ArtifactData


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df
    stats = getattr(data, "synthetic_stats", {})
    starting_cash = getattr(data, "starting_cash", 100_000.0)

    eq_p = df["portfolio_value_s"]
    eq_b = df["benchmark_value_s"]
    eq_c = df["cash_value_s"]

    fig, ax1 = plt.subplots(figsize=theme.FIGSIZE_WIDE)
    fig.patch.set_facecolor(theme.BG)

    # ── Equity curves ────────────────────────────────────────────────────────
    ax1.plot(
        df.index,
        eq_p,
        color=theme.ACCENT,
        linewidth=2.2,
        label=f"Platform  ({stats.get('total_return_pct', '—')}%)",
        zorder=4,
    )
    ax1.plot(
        df.index,
        eq_b,
        color=theme.BLUE,
        linewidth=1.6,
        label=f"SPY Benchmark  ({stats.get('total_return_pct_bench', '—')}%)",
        zorder=3,
    )
    ax1.plot(
        df.index,
        eq_c,
        color=theme.TEXT2,
        linewidth=1.0,
        linestyle=":",
        label=f"Cash / Risk-Free  ({stats.get('risk_free_rate_pct', '—')}% ann.)",
        zorder=2,
    )

    ax1.fill_between(
        df.index, starting_cash, eq_p, where=(eq_p >= starting_cash), alpha=0.08, color=theme.ACCENT
    )
    ax1.fill_between(
        df.index, starting_cash, eq_p, where=(eq_p < starting_cash), alpha=0.12, color=theme.RED
    )

    ax1.set_ylabel("Portfolio Value ($)", color=theme.TEXT)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    # ── Right axis: return % ──────────────────────────────────────────────────
    ax2 = ax1.twinx()
    ax2.set_facecolor(theme.SURFACE)
    ret_p = (eq_p / starting_cash - 1) * 100
    ax2.plot(df.index, ret_p, color=theme.ACCENT, linewidth=0, alpha=0)  # invisible — sets scale
    ax2.set_ylabel("Cumulative Return (%)", color=theme.TEXT2, fontsize=10)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.1f}%"))
    ax2.tick_params(axis="y", colors=theme.TEXT2)
    ax2.spines["right"].set_edgecolor(theme.BORDER)
    ax2.set_ylim(
        (eq_p.min() / starting_cash - 1) * 100 - 5,
        (eq_p.max() / starting_cash - 1) * 100 + 5,
    )

    # ── Timeline event annotations ────────────────────────────────────────────
    for ev in data.timeline_events:
        ev_date = pd.Timestamp(ev.get("date", ""))
        ev_type = ev.get("event_type", "unknown")
        ev_status = ev.get("status", "ok")

        if ev_date not in df.index and ev_date not in df.index:
            # Find nearest tick
            if len(df.index) == 0:
                continue
            ev_date = df.index[np.argmin(np.abs(df.index - ev_date))]

        color = theme.EVENT_COLORS.get(ev_type, theme.TEXT2)
        alpha = 0.5 if ev_status == "skipped" else 1.0

        # Vertical line
        ax1.axvline(ev_date, color=color, linewidth=0.8, alpha=alpha * 0.5, linestyle="--")

        # Marker at top of chart
        ax1.annotate(
            "",
            xy=(mdates.date2num(ev_date), ax1.get_ylim()[1]),
            xycoords=("data", "data"),
        )
        # Small label
        ax1.text(
            mdates.date2num(ev_date),
            ax1.get_ylim()[0] + (ax1.get_ylim()[1] - ax1.get_ylim()[0]) * 0.02,
            "▲",
            ha="center",
            va="bottom",
            color=color,
            fontsize=7,
            alpha=alpha,
            transform=ax1.transData,
        )

    # ── Target return hurdle lines ────────────────────────────────────────────
    # Internal business benchmarks, not financial advisory standards.
    # Use blended transform: x in axes fraction, y in data coordinates.
    hurdle_styles = [
        (0.15, theme.YELLOW, "15% hurdle"),
        (0.20, theme.ORANGE, "20% hurdle"),
        (0.25, theme.RED, "25% hurdle"),
    ]
    blend = mtransforms.blended_transform_factory(ax1.transAxes, ax1.transData)
    for hurdle_pct, h_color, h_label in hurdle_styles:
        h_val = starting_cash * (1 + hurdle_pct)
        ax1.axhline(h_val, color=h_color, linewidth=0.7, linestyle="-.", alpha=0.45, zorder=1)
        ax1.text(
            0.985,
            h_val,
            f" {h_label}",
            ha="right",
            va="bottom",
            fontsize=7,
            color=h_color,
            alpha=0.8,
            transform=blend,
        )

    # ── Starting capital reference line ──────────────────────────────────────
    ax1.axhline(
        starting_cash, color=theme.BORDER, linewidth=0.8, linestyle="--", alpha=0.6, zorder=1
    )

    # ── Labels ───────────────────────────────────────────────────────────────
    title_return = f"+{stats.get('total_return_pct', '—')}%" if stats else ""
    ax1.set_title(
        f"Platform Equity Curve  {title_return}  |  "
        f"Sharpe {stats.get('sharpe_ratio', '—')}  ·  "
        f"Max DD {stats.get('max_drawdown_pct', '—')}%",
        color=theme.TEXT,
        pad=14,
    )
    theme.subtitle(
        ax1,
        f"{data.start_date} → {data.end_date}  ·  "
        f"Symbols: {', '.join(data.symbols[:6])}  ·  "
        f"Starting capital: ${starting_cash:,.0f}  ·  "
        f"{'[synthetic financial data]' if data.is_synthetic else '[live data]'}",
    )

    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax1.get_xticklabels(), rotation=30, ha="right")

    lines1, labels1 = ax1.get_legend_handles_labels()
    ax1.legend(lines1, labels1, loc="upper left", framealpha=0.85)

    ax1.set_xlim(df.index[0], df.index[-1])

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = out_dir / "01_equity_curve.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
