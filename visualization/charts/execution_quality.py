"""
Chart 8 — Execution Quality

Tells the story: how well did the execution layer perform?

Shows:
  - Slippage distribution (seaborn violin + box)
  - Adverse fill rate over time
  - Fill latency distribution
  - Correlation: slippage vs market volatility (daily return magnitude)

Note: uses synthetic per-tick execution data when artifact has no real fills.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from visualization import theme
from visualization.loader import ArtifactData


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df

    has_synthetic_exec = "avg_slippage_bps_s" in df.columns

    fig = plt.figure(figsize=(16, 8))
    fig.patch.set_facecolor(theme.BG)
    gs = fig.add_gridspec(2, 3, hspace=0.45, wspace=0.35)

    ax_slip_dist = fig.add_subplot(gs[0, 0])
    ax_adverse = fig.add_subplot(gs[0, 1])
    ax_latency = fig.add_subplot(gs[0, 2])
    ax_slip_time = fig.add_subplot(gs[1, :2])
    ax_corr = fig.add_subplot(gs[1, 2])

    data_label = "[synthetic execution data]" if data.is_synthetic else "[live fill data]"
    title_suffix = "  ·  " + data_label

    # ── Slippage distribution ─────────────────────────────────────────────────
    if has_synthetic_exec:
        slippage = df["avg_slippage_bps_s"].dropna()
        sns.histplot(
            slippage,
            ax=ax_slip_dist,
            bins=30,
            kde=True,
            color=theme.ACCENT,
            alpha=0.55,
            line_kws={"linewidth": 1.8},
            edgecolor=theme.BORDER,
        )
        mean_slip = slippage.mean()
        ax_slip_dist.axvline(
            mean_slip, color=theme.YELLOW, linewidth=1.4, label=f"Mean: {mean_slip:.1f} bps"
        )
        ax_slip_dist.axvline(
            slippage.quantile(0.95),
            color=theme.RED,
            linewidth=1.0,
            linestyle="--",
            label=f"95th pct: {slippage.quantile(0.95):.1f} bps",
        )
        ax_slip_dist.legend(fontsize=8)
    else:
        ax_slip_dist.text(
            0.5,
            0.5,
            "No fill data",
            ha="center",
            va="center",
            transform=ax_slip_dist.transAxes,
            color=theme.TEXT2,
        )

    ax_slip_dist.set_title(
        f"Slippage Distribution\n(bps){title_suffix}", color=theme.TEXT, pad=10, fontsize=10
    )
    ax_slip_dist.set_xlabel("Avg Slippage (bps)", fontsize=9)

    # ── Adverse fill rate over time ───────────────────────────────────────────
    if has_synthetic_exec:
        adverse_rate = df["adverse_fill_rate_s"].dropna()
        adverse_pct = adverse_rate * 100
        roll_adverse = adverse_pct.rolling(10, min_periods=1).mean()

        ax_adverse.fill_between(
            df.index[: len(adverse_pct)], adverse_pct, alpha=0.3, color=theme.RED
        )
        ax_adverse.plot(
            df.index[: len(roll_adverse)],
            roll_adverse,
            color=theme.RED,
            linewidth=1.4,
            label="10-day avg",
        )

        overall_rate = adverse_pct.mean()
        ax_adverse.axhline(
            overall_rate,
            color=theme.YELLOW,
            linewidth=0.8,
            linestyle="--",
            label=f"Avg: {overall_rate:.1f}%",
        )
        ax_adverse.legend(fontsize=8)
    else:
        ax_adverse.text(
            0.5,
            0.5,
            "No fill data",
            ha="center",
            va="center",
            transform=ax_adverse.transAxes,
            color=theme.TEXT2,
        )

    import matplotlib.dates as mdates

    ax_adverse.set_title("Adverse Fill Rate (%)", color=theme.TEXT, pad=10, fontsize=10)
    ax_adverse.set_ylabel("%", fontsize=9)
    ax_adverse.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax_adverse.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax_adverse.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax_adverse.get_xticklabels(), rotation=30, ha="right", fontsize=7)

    # ── Fill latency distribution ─────────────────────────────────────────────
    if has_synthetic_exec:
        latency = df["avg_latency_ms_s"].dropna()
        sns.histplot(
            latency,
            ax=ax_latency,
            bins=25,
            kde=True,
            color=theme.BLUE,
            alpha=0.55,
            line_kws={"linewidth": 1.6},
            edgecolor=theme.BORDER,
        )
        mean_lat = latency.mean()
        ax_latency.axvline(
            mean_lat, color=theme.YELLOW, linewidth=1.3, label=f"Mean: {mean_lat:.0f} ms"
        )
        ax_latency.legend(fontsize=8)
    else:
        ax_latency.text(
            0.5,
            0.5,
            "No fill data",
            ha="center",
            va="center",
            transform=ax_latency.transAxes,
            color=theme.TEXT2,
        )

    ax_latency.set_title("Fill Latency (ms)", color=theme.TEXT, pad=10, fontsize=10)
    ax_latency.set_xlabel("Submission Latency (ms)", fontsize=9)

    # ── Slippage over time ────────────────────────────────────────────────────
    if has_synthetic_exec:
        slippage_ts = df["avg_slippage_bps_s"].dropna()
        roll_slip = slippage_ts.rolling(15, min_periods=1).mean()

        ax_slip_time.fill_between(
            df.index[: len(slippage_ts)], slippage_ts, alpha=0.2, color=theme.ACCENT
        )
        ax_slip_time.plot(
            df.index[: len(roll_slip)],
            roll_slip,
            color=theme.ACCENT,
            linewidth=1.4,
            label="15-day rolling avg",
        )
        ax_slip_time.axhline(
            slippage_ts.mean(),
            color=theme.YELLOW,
            linewidth=0.8,
            linestyle="--",
            label=f"Overall avg: {slippage_ts.mean():.1f} bps",
        )

        # Shade halted periods
        for ev in data.timeline_events:
            if ev.get("event_type") in ("safety_emergency_halt", "controls_paused"):
                ev_date = pd.Timestamp(ev.get("date", ""))
                ax_slip_time.axvspan(
                    mdates.date2num(ev_date),
                    mdates.date2num(ev_date) + 5,
                    alpha=0.12,
                    color=theme.RED,
                )

        ax_slip_time.legend(fontsize=8, loc="upper right")
    else:
        ax_slip_time.text(
            0.5,
            0.5,
            "No fill data",
            ha="center",
            va="center",
            transform=ax_slip_time.transAxes,
            color=theme.TEXT2,
        )

    ax_slip_time.set_title("Slippage Over Time (bps)", color=theme.TEXT, pad=10, fontsize=10)
    ax_slip_time.set_ylabel("Avg Slippage (bps)", fontsize=9)
    ax_slip_time.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_slip_time.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax_slip_time.get_xticklabels(), rotation=30, ha="right", fontsize=7)
    ax_slip_time.set_xlim(df.index[0], df.index[-1])

    # ── Slippage vs volatility scatter ────────────────────────────────────────
    if has_synthetic_exec and "daily_return_s" in df.columns:
        vol_proxy = df["daily_return_s"].abs().dropna() * 100
        slip_align = df["avg_slippage_bps_s"].dropna()
        common_idx = vol_proxy.index.intersection(slip_align.index)

        if len(common_idx) > 10:
            x = vol_proxy.loc[common_idx]
            y = slip_align.loc[common_idx]

            ax_corr.scatter(x, y, color=theme.ACCENT, alpha=0.3, s=12)
            # Trend line
            z = np.polyfit(x, y, 1)
            p = np.poly1d(z)
            xline = np.linspace(x.min(), x.max(), 50)
            ax_corr.plot(xline, p(xline), color=theme.YELLOW, linewidth=1.4)

            corr = x.corr(y)
            ax_corr.text(
                0.05,
                0.92,
                f"ρ = {corr:.2f}",
                transform=ax_corr.transAxes,
                fontsize=9,
                color=theme.YELLOW,
            )
    else:
        ax_corr.text(
            0.5,
            0.5,
            "No data",
            ha="center",
            va="center",
            transform=ax_corr.transAxes,
            color=theme.TEXT2,
        )

    ax_corr.set_title("Slippage vs Volatility", color=theme.TEXT, pad=10, fontsize=10)
    ax_corr.set_xlabel("|Daily Return| (%)", fontsize=9)
    ax_corr.set_ylabel("Slippage (bps)", fontsize=9)

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = out_dir / "08_execution_quality.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
