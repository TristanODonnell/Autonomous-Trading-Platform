"""
Chart 11 — Rolling Risk Metrics Dashboard

Tells the story: were results stable over time, or driven by one lucky period?

Four panels (shared x-axis):
  1. Rolling 30-day return (%)
  2. Rolling 30-day annualized volatility (%)
  3. Rolling 30-day Sharpe ratio
  4. Rolling drawdown from peak (%)

Platform = green, Benchmark = blue where both are available.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from visualization import theme
from visualization.loader import ArtifactData
from visualization.metrics import (
    rolling_drawdown,
    rolling_returns,
    rolling_sharpe,
    rolling_volatility,
)

_WINDOW = 30  # trading days


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df
    stats = getattr(data, "synthetic_stats", {})
    rfr = (stats.get("risk_free_rate_pct") or 5.3) / 100

    has_benchmark = "benchmark_value_s" in df.columns and "benchmark_return_s" in df.columns

    ret_p = (
        df["daily_return_s"].copy() if "daily_return_s" in df.columns else pd.Series(dtype=float)
    )
    eq_p = (
        df["portfolio_value_s"].copy()
        if "portfolio_value_s" in df.columns
        else pd.Series(dtype=float)
    )

    ret_b = df["benchmark_return_s"].copy() if has_benchmark else None
    eq_b = df["benchmark_value_s"].copy() if has_benchmark else None

    if len(ret_p) < 10:
        _render_unavailable(data, out_dir)
        return out_dir / "11_rolling_risk.png"

    # ── Compute rolling series ────────────────────────────────────────────────
    roll_ret_p = rolling_returns(eq_p, _WINDOW) * 100
    roll_vol_p = rolling_volatility(ret_p, _WINDOW) * 100
    roll_shr_p = rolling_sharpe(ret_p, _WINDOW, rfr)
    roll_dd_p = rolling_drawdown(eq_p) * 100

    roll_ret_b = roll_vol_b = roll_shr_b = roll_dd_b = None
    if ret_b is not None and eq_b is not None:
        roll_ret_b = rolling_returns(eq_b, _WINDOW) * 100
        roll_vol_b = rolling_volatility(ret_b, _WINDOW) * 100
        roll_shr_b = rolling_sharpe(ret_b, _WINDOW, rfr)
        roll_dd_b = rolling_drawdown(eq_b) * 100

    # ── Figure ────────────────────────────────────────────────────────────────
    n_panels = 4
    fig, axes = plt.subplots(
        n_panels,
        1,
        figsize=(16, 11),
        sharex=True,
        gridspec_kw={"hspace": 0.12},
    )
    fig.patch.set_facecolor(theme.BG)

    _panel_rolling_return(axes[0], df.index, roll_ret_p, roll_ret_b)
    _panel_rolling_vol(axes[1], df.index, roll_vol_p, roll_vol_b)
    _panel_rolling_sharpe(axes[2], df.index, roll_shr_p, roll_shr_b)
    _panel_rolling_drawdown(axes[3], df.index, roll_dd_p, roll_dd_b)

    # ── x-axis on last panel ──────────────────────────────────────────────────
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(axes[-1].get_xticklabels(), rotation=30, ha="right")

    fig.suptitle(
        f"Rolling Risk Metrics  ({_WINDOW}-day window)",
        color=theme.TEXT,
        fontsize=14,
        y=0.99,
    )
    theme.subtitle(
        axes[0],
        f"{data.start_date} → {data.end_date}  ·  "
        f"{'[synthetic financial data]' if data.is_synthetic else '[live data]'}  ·  "
        f"Green = Platform, Blue = Synthetic Benchmark",
    )

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.98])

    out = out_dir / "11_rolling_risk.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# Panel helpers
# ---------------------------------------------------------------------------


def _panel_rolling_return(ax, dates, platform, benchmark) -> None:
    ax.plot(dates, platform, color=theme.ACCENT, linewidth=1.6, label="Platform")
    ax.fill_between(dates, platform, 0, where=(platform > 0), alpha=0.08, color=theme.ACCENT)
    ax.fill_between(dates, platform, 0, where=(platform < 0), alpha=0.12, color=theme.RED)
    if benchmark is not None:
        ax.plot(
            dates,
            benchmark,
            color=theme.BLUE,
            linewidth=1.0,
            alpha=0.7,
            linestyle="--",
            label="Benchmark",
        )
    ax.axhline(0, color=theme.BORDER, linewidth=0.8)
    ax.set_ylabel("30d Return (%)", color=theme.TEXT2, fontsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    ax.legend(loc="upper left", fontsize=8)


def _panel_rolling_vol(ax, dates, platform, benchmark) -> None:
    ax.plot(dates, platform, color=theme.YELLOW, linewidth=1.6, label="Platform vol")
    if benchmark is not None:
        ax.plot(
            dates,
            benchmark,
            color=theme.BLUE,
            linewidth=1.0,
            alpha=0.7,
            linestyle="--",
            label="Benchmark vol",
        )
    ax.set_ylabel("30d Ann. Vol (%)", color=theme.TEXT2, fontsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(loc="upper left", fontsize=8)


def _panel_rolling_sharpe(ax, dates, platform, benchmark) -> None:
    ax.plot(dates, platform, color=theme.ACCENT, linewidth=1.6, label="Platform Sharpe")
    ax.fill_between(dates, platform, 0, where=(platform > 0), alpha=0.08, color=theme.ACCENT)
    ax.fill_between(dates, platform, 0, where=(platform < 0), alpha=0.12, color=theme.RED)
    if benchmark is not None:
        ax.plot(
            dates,
            benchmark,
            color=theme.BLUE,
            linewidth=1.0,
            alpha=0.7,
            linestyle="--",
            label="Benchmark Sharpe",
        )
    ax.axhline(0, color=theme.BORDER, linewidth=0.8)
    ax.axhline(1, color=theme.TEXT2, linewidth=0.6, linestyle=":", alpha=0.5)
    ax.set_ylabel("30d Sharpe", color=theme.TEXT2, fontsize=9)
    ax.legend(loc="upper left", fontsize=8)


def _panel_rolling_drawdown(ax, dates, platform, benchmark) -> None:
    ax.fill_between(dates, platform, 0, where=(platform < 0), alpha=0.25, color=theme.RED)
    ax.plot(dates, platform, color=theme.RED, linewidth=1.6, label="Platform DD")
    if benchmark is not None:
        ax.plot(
            dates,
            benchmark,
            color=theme.BLUE,
            linewidth=1.0,
            alpha=0.7,
            linestyle="--",
            label="Benchmark DD",
        )
    ax.axhline(0, color=theme.BORDER, linewidth=0.8)
    ax.set_ylabel("Drawdown (%)", color=theme.TEXT2, fontsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.legend(loc="lower left", fontsize=8)


def _render_unavailable(data: ArtifactData, out_dir: Path) -> None:
    theme.apply()
    fig, ax = plt.subplots(figsize=theme.FIGSIZE_WIDE)
    fig.patch.set_facecolor(theme.BG)
    ax.text(
        0.5,
        0.5,
        "Rolling Risk Metrics — Insufficient data\n"
        "(requires at least 10 trading days with return data)",
        ha="center",
        va="center",
        transform=ax.transAxes,
        fontsize=13,
        color=theme.TEXT2,
    )
    ax.set_title("Rolling Risk Metrics", color=theme.TEXT)
    theme.watermark(fig)
    fig.savefig(out_dir / "11_rolling_risk.png", dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
