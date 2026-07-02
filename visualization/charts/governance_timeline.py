"""
Chart 5 — Governance & Events Timeline

Tells the story: what decisions were made, and when?

Shows:
  - Horizontal timeline of all applied timeline events
  - Colour-coded by event type
  - Equity curve overlay so events are in financial context
  - Governance totals (promotions, demotions, health transitions)
  - Per-tick governance state (strategies in breach)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import pandas as pd

from visualization import theme
from visualization.loader import ArtifactData


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    df = data.tick_df
    timeline_events = data.timeline_events
    gov = data.governance_summary
    starting_cash = getattr(data, "starting_cash", 100_000.0)

    fig, (ax_equity, ax_events, ax_breach) = plt.subplots(
        3,
        1,
        figsize=(16, 10),
        gridspec_kw={"height_ratios": [2.5, 2, 0.8], "hspace": 0.08},
        sharex=True,
    )
    fig.patch.set_facecolor(theme.BG)

    # ── Top: equity curve context ──────────────────────────────────────────────
    eq = df["portfolio_value_s"]
    ax_equity.plot(df.index, eq, color=theme.ACCENT, linewidth=1.8)
    ax_equity.fill_between(
        df.index, starting_cash, eq, where=(eq >= starting_cash), alpha=0.07, color=theme.ACCENT
    )
    ax_equity.fill_between(
        df.index, starting_cash, eq, where=(eq < starting_cash), alpha=0.10, color=theme.RED
    )
    ax_equity.axhline(starting_cash, color=theme.BORDER, linewidth=0.7, linestyle="--")
    ax_equity.set_ylabel("Equity ($)", color=theme.TEXT)
    ax_equity.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax_equity.set_title(
        f"Platform Governance Timeline  |  "
        f"Promotions: {gov.get('promotions_executed', 0)}  ·  "
        f"Demotions: {gov.get('demotions_executed', 0)}  ·  "
        f"Health Transitions: {gov.get('health_transitions', 0)}",
        color=theme.TEXT,
        pad=14,
    )
    theme.subtitle(
        ax_equity,
        f"{data.start_date} → {data.end_date}  ·  "
        f"Active strategies: {data.strategy_catalog.get('total_active', '—')}  ·  "
        f"{'[synthetic equity]' if data.is_synthetic else '[live]'}",
    )

    # Draw event verticals on equity chart
    for ev in timeline_events:
        ev_date = pd.Timestamp(ev.get("date", ""))
        ev_type = ev.get("event_type", "")
        color = theme.EVENT_COLORS.get(ev_type, theme.TEXT2)
        ax_equity.axvline(ev_date, color=color, linewidth=0.9, alpha=0.5, linestyle="--", zorder=3)

    # ── Middle: event swim lanes ──────────────────────────────────────────────
    ax_events.set_facecolor(theme.SURFACE)

    # Group event types into swim lanes
    event_categories = [
        (
            "Safety & Controls",
            {
                "safety_emergency_halt",
                "safety_release_halt",
                "controls_paused",
                "controls_resumed",
                "trading_enabled",
                "trading_disabled",
                "trading_mode_changed",
            },
        ),
        (
            "Governance",
            {
                "governance_manual_transition",
                "governance_auto_promotion",
                "governance_auto_demotion",
                "health_review_acknowledged",
            },
        ),
        ("Settings", {"settings_changed", "settings_seeded", "settings_reset_defaults"}),
        (
            "Allocation",
            {
                "allocation_override_set",
                "allocation_override_cleared",
                "strategy_disabled",
                "strategy_enabled",
            },
        ),
    ]

    lane_y = {cat: i for i, (cat, _) in enumerate(event_categories)}
    n_lanes = len(event_categories)

    # Draw lane separators
    for i in range(n_lanes + 1):
        ax_events.axhline(i - 0.5, color=theme.BORDER, linewidth=0.5, alpha=0.5)

    # Draw lane labels
    for cat, _ in event_categories:
        y = lane_y[cat]
        ax_events.text(
            df.index[0],
            y,
            f"  {cat}",
            va="center",
            ha="left",
            fontsize=8.5,
            color=theme.TEXT2,
            zorder=5,
        )

    # Plot events
    plotted_for_legend = {}
    for ev in timeline_events:
        ev_date = pd.Timestamp(ev.get("date", ""))
        ev_type = ev.get("event_type", "")
        ev_status = ev.get("status", "ok")
        color = theme.EVENT_COLORS.get(ev_type, theme.TEXT2)
        label = theme.EVENT_LABELS.get(ev_type, ev_type.replace("_", " ").title())

        # Find which lane
        lane = None
        for cat, types in event_categories:
            if ev_type in types:
                lane = lane_y[cat]
                break
        if lane is None:
            lane = n_lanes - 1

        marker = "o" if ev_status != "skipped" else "x"
        alpha = 1.0 if ev_status != "skipped" else 0.4
        size = 90 if ev_status != "skipped" else 50

        ax_events.scatter(
            ev_date,
            lane,
            color=color,
            marker=marker,
            s=size,
            zorder=5,
            alpha=alpha,
            edgecolors=theme.BG,
            linewidths=0.8,
        )

        # Label the event
        ax_events.annotate(
            label,
            xy=(ev_date, lane),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=7,
            color=color,
            alpha=alpha,
            rotation=60,
            va="bottom",
        )

        if ev_type not in plotted_for_legend and ev_status != "skipped":
            plotted_for_legend[ev_type] = (color, label)

    ax_events.set_ylim(-0.6, n_lanes - 0.4)
    ax_events.set_yticks(list(lane_y.values()))
    ax_events.set_yticklabels([c for c, _ in event_categories], fontsize=9)
    ax_events.set_ylabel("")

    # Legend for event status
    ok_handle = mlines.Line2D(
        [], [], color=theme.TEXT, marker="o", markersize=6, linestyle="None", label="Applied"
    )
    skip_handle = mlines.Line2D(
        [],
        [],
        color=theme.TEXT2,
        marker="x",
        markersize=6,
        linestyle="None",
        label="Skipped",
        alpha=0.6,
    )
    ax_events.legend(
        handles=[ok_handle, skip_handle], loc="upper right", fontsize=8, framealpha=0.7
    )

    # ── Bottom: strategies in breach per tick ─────────────────────────────────
    breach_counts = df["strategies_in_breach_count"].fillna(0)
    ax_breach.fill_between(df.index, breach_counts, 0, color=theme.RED, alpha=0.6, step="mid")
    ax_breach.set_ylabel("In Breach", color=theme.TEXT2, fontsize=8)
    ax_breach.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_breach.tick_params(axis="y", labelsize=8)

    ax_breach.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_breach.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax_breach.get_xticklabels(), rotation=30, ha="right", fontsize=8)

    ax_equity.set_xlim(df.index[0], df.index[-1])
    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = out_dir / "05_governance_timeline.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
