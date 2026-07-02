"""
Chart 2 — Drawdown Analysis

Tells the story: how deep did the platform fall and for how long?

Shows:
  - Platform drawdown (red fill)
  - Benchmark drawdown (blue line)
  - Drawdown ladder threshold lines (WARNING / PROBATION / SUSPENDED)
  - Risk block events (vertical markers)
  - Annotation of max drawdown point
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from visualization import theme
from visualization.loader import ArtifactData

# Drawdown ladder thresholds (from governance ladder)
_THRESHOLDS = [
    ("WARNING", -0.50, theme.YELLOW),
    ("PROBATION", -0.75, theme.ORANGE),
    ("SUSPENDED", -0.90, theme.RED),
]


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df
    stats = getattr(data, "synthetic_stats", {})
    dd_p = df["drawdown_s"] * 100  # as percentage
    dd_b = df["benchmark_drawdown_s"] * 100

    fig, (ax_dd, ax_ud) = plt.subplots(
        2,
        1,
        figsize=theme.FIGSIZE_WIDE,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08},
        sharex=True,
    )
    fig.patch.set_facecolor(theme.BG)

    # ── Top panel: drawdown curves ────────────────────────────────────────────
    ax_dd.fill_between(df.index, dd_p, 0, where=(dd_p < 0), color=theme.RED, alpha=0.25, zorder=2)
    ax_dd.plot(
        df.index,
        dd_p,
        color=theme.RED,
        linewidth=1.8,
        label=f"Platform  (Max DD {stats.get('max_drawdown_pct', '—')}%)",
        zorder=3,
    )
    ax_dd.plot(
        df.index,
        dd_b,
        color=theme.BLUE,
        linewidth=1.2,
        linestyle="--",
        label=f"SPY Benchmark  (Max DD {stats.get('max_drawdown_pct_bench', '—')}%)",
        zorder=3,
        alpha=0.7,
    )

    # Ladder threshold lines scaled to strategy-level limits (not portfolio)
    # Show at 50% / 75% / 90% of max_drawdown_limit (15% default)
    max_dd_limit = float(data.settings_summary.get("max_drawdown_limit", "0.15") or "0.15") * 100

    for label, frac, color in _THRESHOLDS:
        threshold_pct = frac * max_dd_limit  # e.g. -50% of 15% = -7.5%
        ax_dd.axhline(threshold_pct, color=color, linewidth=0.8, linestyle=":", alpha=0.7, zorder=1)
        ax_dd.text(
            df.index[-1],
            threshold_pct + 0.15,
            f"  {label}",
            color=color,
            fontsize=7.5,
            va="bottom",
            ha="right",
        )

    # Annotate max drawdown point with peak / trough / recovery details
    if len(dd_p) > 0 and not dd_p.isna().all():
        from visualization.metrics import max_drawdown_details

        eq_p_series = df.get("portfolio_value_s", dd_p)  # use equity if available
        dd_details = (
            max_drawdown_details(eq_p_series)
            if "portfolio_value_s" in df.columns
            else {
                "max_dd_pct": float(dd_p.min()),
                "peak_date": None,
                "trough_date": dd_p.idxmin(),
                "recovery_date": None,
                "peak_to_trough_days": 0,
                "trough_to_recovery_days": None,
                "recovered": False,
            }
        )

        worst_idx = dd_details["trough_date"] or dd_p.idxmin()
        worst_val = dd_p.min()

        # Arrow annotation at the trough
        recovery_note = (
            f"Recovered in {dd_details['trough_to_recovery_days']}d"
            if dd_details.get("recovered") and dd_details.get("trough_to_recovery_days")
            else "No full recovery in period"
        )
        peak_note = f"Peak: {dd_details['peak_date'].strftime('%b %d') if dd_details.get('peak_date') else '—'}"
        ax_dd.annotate(
            f"Max DD: {worst_val:.1f}%\n"
            f"{peak_note}  ·  {dd_details.get('peak_to_trough_days', 0)}d to trough\n"
            f"{recovery_note}",
            xy=(worst_idx, worst_val),
            xytext=(15, -28),
            textcoords="offset points",
            color=theme.RED,
            fontsize=8,
            arrowprops=dict(arrowstyle="->", color=theme.RED, lw=1.0),
            bbox=dict(
                boxstyle="round,pad=0.3", facecolor=theme.SURFACE2, edgecolor=theme.RED, alpha=0.85
            ),
        )

        # Mark the peak date with a small vertical line
        if dd_details.get("peak_date"):
            ax_dd.axvline(
                dd_details["peak_date"], color=theme.ACCENT, linewidth=0.8, linestyle=":", alpha=0.6
            )
            ax_dd.text(
                dd_details["peak_date"], 0.3, "Peak", color=theme.ACCENT, fontsize=7, ha="center"
            )

        # Mark the recovery date if it exists
        if dd_details.get("recovery_date"):
            ax_dd.axvline(
                dd_details["recovery_date"],
                color=theme.ACCENT,
                linewidth=0.8,
                linestyle=":",
                alpha=0.6,
            )
            ax_dd.text(
                dd_details["recovery_date"],
                0.3,
                "Recovered",
                color=theme.ACCENT,
                fontsize=7,
                ha="center",
            )

    # Risk block events
    blocked_days = df[df["is_blocked"]]
    for bd in blocked_days.index:
        ax_dd.axvline(bd, color=theme.RED, linewidth=1.0, alpha=0.5, zorder=4)

    # Safety halts and controls pauses from timeline
    for ev in data.timeline_events:
        ev_type = ev.get("event_type", "")
        if ev_type not in ("safety_emergency_halt", "controls_paused"):
            continue
        ev_date = pd.Timestamp(ev.get("date", ""))
        color = theme.EVENT_COLORS.get(ev_type, theme.RED)
        ax_dd.axvspan(
            mdates.date2num(ev_date),
            mdates.date2num(ev_date) + 5,
            alpha=0.12,
            color=color,
            zorder=1,
        )
        ax_dd.text(
            ev_date,
            ax_dd.get_ylim()[0] * 0.85 if ax_dd.get_ylim()[0] < 0 else -0.5,
            "●",
            ha="center",
            fontsize=8,
            color=color,
            zorder=5,
        )

    ax_dd.set_ylabel("Drawdown (%)", color=theme.TEXT)
    ax_dd.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax_dd.axhline(0, color=theme.BORDER, linewidth=0.8, zorder=1)
    ax_dd.set_title(
        f"Drawdown Analysis  |  Max DD {stats.get('max_drawdown_pct', '—')}%  ·  "
        f"Duration {stats.get('max_dd_duration_days', '—')} days",
        color=theme.TEXT,
        pad=14,
    )
    ax_dd.legend(loc="lower left")

    # ── Bottom panel: underwater duration ────────────────────────────────────
    in_drawdown = (dd_p < -0.1).astype(int)
    ax_ud.fill_between(df.index, in_drawdown, 0, color=theme.RED, alpha=0.4, step="mid")
    ax_ud.set_ylabel("Underwater", color=theme.TEXT2, fontsize=9)
    ax_ud.set_yticks([0, 1])
    ax_ud.set_yticklabels(["No", "Yes"], fontsize=8, color=theme.TEXT2)
    ax_ud.set_ylim(-0.1, 1.5)

    ax_ud.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_ud.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax_ud.get_xticklabels(), rotation=30, ha="right")

    theme.subtitle(
        ax_dd,
        f"{data.start_date} → {data.end_date}  ·  "
        f"Max DD limit (settings): {max_dd_limit:.0f}%  ·  "
        f"{'[synthetic]' if data.is_synthetic else '[live]'}",
    )

    ax_dd.set_xlim(df.index[0], df.index[-1])
    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = out_dir / "02_drawdown.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
