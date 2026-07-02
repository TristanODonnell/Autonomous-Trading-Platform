"""
Chart 6 — Platform Operational Health

Tells the story: did the platform run reliably?

Shows:
  - Tick success rate calendar heatmap (seaborn)
  - System health status per tick (degraded/ok/critical)
  - Alert counts over time
  - Tick completion summary (attempted / ok / failed)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd

from visualization import theme
from visualization.loader import ArtifactData

_HEALTH_COLORS = {
    "ok": theme.ACCENT,
    "degraded": theme.YELLOW,
    "critical": theme.RED,
    "unknown": theme.TEXT2,
}
_HEALTH_VALUES = {"ok": 2, "degraded": 1, "critical": 0, "unknown": 1}


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df

    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor(theme.BG)
    gs = fig.add_gridspec(3, 2, height_ratios=[2.5, 1.5, 1], hspace=0.38, wspace=0.25)

    ax_cal = fig.add_subplot(gs[0, :])
    ax_health = fig.add_subplot(gs[1, :])
    ax_alerts = fig.add_subplot(gs[2, 0])
    ax_stats = fig.add_subplot(gs[2, 1])

    # ── Calendar heatmap ──────────────────────────────────────────────────────
    df_cal = pd.DataFrame(
        {
            "date": df.index,
            "has_error": df["has_errors"].astype(int),
            "events": df["timeline_events_this_tick"],
        }
    )
    df_cal["week"] = df_cal["date"].dt.isocalendar().week.astype(int)
    df_cal["dow"] = df_cal["date"].dt.dayofweek  # 0=Mon
    df_cal["month_label"] = df_cal["date"].dt.strftime("%b")

    # Build pivot: rows=DOW, cols=week
    pivot = df_cal.pivot_table(
        index="dow",
        columns="week",
        values="has_error",
        aggfunc="max",
    ).fillna(-1)

    for row_i, dow in enumerate(pivot.index):
        for col_j, week in enumerate(pivot.columns):
            val = pivot.loc[dow, week]
            color = theme.SURFACE2 if val < 0 else (theme.RED if val > 0 else theme.ACCENT)
            ax_cal.add_patch(
                mpatches.Rectangle(
                    (col_j, row_i),
                    0.9,
                    0.9,
                    color=color,
                    alpha=0.85,
                )
            )

    n_weeks = len(pivot.columns)
    ax_cal.set_xlim(-0.1, n_weeks)
    ax_cal.set_ylim(-0.1, 5.5)
    ax_cal.set_yticks([0.45, 1.45, 2.45, 3.45, 4.45])
    ax_cal.set_yticklabels(["Mon", "Tue", "Wed", "Thu", "Fri"], fontsize=8)
    ax_cal.set_xticks([])
    ax_cal.set_title(
        "Tick Execution Calendar  (green = ok, red = errors)", color=theme.TEXT, pad=12
    )
    theme.subtitle(
        ax_cal,
        f"{data.start_date} → {data.end_date}  ·  "
        f"{data.ticks_ok}/{data.ticks_attempted} ticks succeeded  "
        f"({'100.0' if data.ticks_attempted == 0 else f'{data.ticks_ok / data.ticks_attempted * 100:.1f}'}% success rate)",
    )

    # Month labels on x-axis
    month_positions = (
        df_cal.groupby("month_label")["week"]
        .first()
        .reindex(pd.Series(df_cal["date"].dt.strftime("%b").unique()))
    )
    for label, week_num in month_positions.items():
        col_j = list(pivot.columns).index(week_num) if week_num in pivot.columns else 0
        ax_cal.text(col_j, -0.05, label, ha="left", fontsize=7.5, color=theme.TEXT2)

    legend_handles = [
        mpatches.Patch(color=theme.ACCENT, label="Tick OK"),
        mpatches.Patch(color=theme.RED, label="Tick had errors"),
        mpatches.Patch(color=theme.SURFACE2, label="No data"),
    ]
    ax_cal.legend(handles=legend_handles, loc="upper right", fontsize=8)

    # ── System health timeline ────────────────────────────────────────────────
    colors = [_HEALTH_COLORS.get(h, theme.TEXT2) for h in df["system_health"]]

    ax_health.bar(df.index, [1] * len(df), width=1.0, color=colors, alpha=0.8)
    ax_health.set_yticks([])
    ax_health.set_title("System Health Status per Tick", color=theme.TEXT, pad=8, fontsize=11)

    health_legend = [
        mpatches.Patch(color=theme.ACCENT, label="OK"),
        mpatches.Patch(color=theme.YELLOW, label="Degraded"),
        mpatches.Patch(color=theme.RED, label="Critical"),
    ]
    ax_health.legend(handles=health_legend, loc="upper right", fontsize=8)
    ax_health.set_xlim(df.index[0], df.index[-1])

    import matplotlib.dates as mdates

    ax_health.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_health.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax_health.get_xticklabels(), rotation=30, ha="right", fontsize=8)

    # ── Alert counts ──────────────────────────────────────────────────────────
    ax_alerts.fill_between(
        df.index,
        df["active_alerts"].fillna(0),
        color=theme.YELLOW,
        alpha=0.5,
        label="Active Alerts",
    )
    ax_alerts.fill_between(
        df.index,
        df["critical_alerts"].fillna(0),
        color=theme.RED,
        alpha=0.6,
        label="Critical Alerts",
    )
    ax_alerts.set_title("Alert Counts", color=theme.TEXT, pad=8, fontsize=10)
    ax_alerts.set_ylabel("Count", fontsize=9)
    ax_alerts.legend(fontsize=8)
    ax_alerts.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax_alerts.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax_alerts.get_xticklabels(), rotation=30, ha="right", fontsize=7)

    # ── Runtime stats donut ────────────────────────────────────────────────────
    total = max(data.ticks_attempted, 1)
    ok = data.ticks_ok
    failed = data.ticks_failed
    sizes = [ok, failed, max(0, total - ok - failed)]
    colors_d = [theme.ACCENT, theme.RED, theme.SURFACE2]
    labels_d = [f"OK ({ok})", f"Failed ({failed})", "—"]
    wedges, texts = ax_stats.pie(
        [max(s, 0.001) for s in sizes],
        colors=colors_d,
        startangle=90,
        wedgeprops=dict(width=0.45, edgecolor=theme.BG),
    )
    ax_stats.set_title(
        f"Tick Completion\n{ok}/{total} ({ok / total * 100:.1f}%)",
        color=theme.TEXT,
        pad=8,
        fontsize=10,
    )
    ax_stats.legend(
        wedges[:2],
        labels_d[:2],
        loc="lower center",
        fontsize=8,
        framealpha=0.6,
    )

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = out_dir / "06_operational_health.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
