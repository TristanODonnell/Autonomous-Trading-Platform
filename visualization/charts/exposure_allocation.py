"""
Chart 12 — Exposure & Allocation

Tells the story: what was the platform actually holding or exposed to?

This chart visualizes available exposure data from the artifact.

What IS available in this artifact type:
  - End-of-run risk snapshot: gross_exposure, net_exposure
  - Per-tick: open_positions count, cash_balance vs portfolio_value
  - Per-tick: is_blocked flag

What is NOT available (requires strategy-level data):
  - Per-symbol position weights over time
  - Target weight history
  - Allocation override details
  - Per-strategy exposure breakdown

When per-tick holdings are absent, the chart shows:
  1. Cash vs invested exposure over time (derived from portfolio_value - cash_balance)
  2. Position count over time
  3. A summary panel explaining what fields are missing

If the data is synthetic (no real fills), cash = 100% of portfolio throughout,
which is shown with a clear "no positions" note.
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
    risk = data.risk_summary or {}
    starting_cash = getattr(data, "starting_cash", 100_000.0)

    # ── Detect available fields ───────────────────────────────────────────────
    has_portfolio_value = "portfolio_value" in df.columns and df["portfolio_value"].notna().any()
    has_cash_balance = "cash_balance" in df.columns and df["cash_balance"].notna().any()
    has_open_positions = "open_positions" in df.columns
    has_synthetic_eq = "portfolio_value_s" in df.columns

    gross_exposure = risk.get("gross_exposure")
    net_exposure = risk.get("net_exposure")

    has_end_exposure = gross_exposure not in (None, "", "0", 0) or net_exposure not in (
        None,
        "",
        "0",
        0,
    )

    # ── Figure layout ─────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor(theme.BG)
    gs = fig.add_gridspec(2, 2, hspace=0.45, wspace=0.35)

    ax_cash = fig.add_subplot(gs[0, :])  # full-width: cash vs invested over time
    ax_pos = fig.add_subplot(gs[1, 0])  # position count over time
    ax_snap = fig.add_subplot(gs[1, 1])  # end-of-run exposure snapshot

    # ── Top: Cash vs invested exposure ────────────────────────────────────────
    if has_portfolio_value and has_cash_balance:
        pv = df["portfolio_value"].ffill().fillna(starting_cash)
        cash = df["cash_balance"].ffill().fillna(starting_cash)
        invested = (pv - cash).clip(lower=0)
        cash_pct = (cash / pv.replace(0, np.nan) * 100).clip(0, 100)
        inv_pct = (invested / pv.replace(0, np.nan) * 100).clip(0, 100)

        ax_cash.stackplot(
            df.index,
            inv_pct,
            cash_pct,
            labels=["Invested (%)", "Cash (%)"],
            colors=[theme.ACCENT, theme.TEXT2],
            alpha=0.6,
        )
        ax_cash.set_ylabel("Portfolio Allocation (%)", color=theme.TEXT)
        ax_cash.set_ylim(0, 105)
        ax_cash.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax_cash.legend(loc="upper right", fontsize=9)
        _fmt_xaxis(ax_cash)
        ax_cash.set_title(
            "Cash vs Invested Allocation Over Time  (from real portfolio_value & cash_balance)",
            color=theme.TEXT,
            pad=10,
            fontsize=12,
        )
    elif has_synthetic_eq:
        # Synthetic: cash = 100% throughout (no real positions)
        ax_cash.fill_between(
            df.index, 100, color=theme.TEXT2, alpha=0.4, label="Cash 100% (no real fills)"
        )
        ax_cash.set_ylabel("Portfolio Allocation (%)", color=theme.TEXT)
        ax_cash.set_ylim(0, 110)
        ax_cash.set_title(
            "Exposure Over Time — No real fills; all cash (synthetic run)",
            color=theme.TEXT,
            pad=10,
            fontsize=12,
        )
        ax_cash.text(
            0.5,
            0.5,
            "No real positions recorded.\n"
            "This is a synthetic/replay run with total_fills = 0.\n"
            "Cash allocation = 100% throughout the period.",
            ha="center",
            va="center",
            transform=ax_cash.transAxes,
            fontsize=11,
            color=theme.TEXT2,
            style="italic",
        )
        ax_cash.legend(loc="upper right", fontsize=9)
        _fmt_xaxis(ax_cash)
    else:
        ax_cash.text(
            0.5,
            0.5,
            "Portfolio value data unavailable",
            ha="center",
            va="center",
            transform=ax_cash.transAxes,
            fontsize=12,
            color=theme.TEXT2,
        )
        ax_cash.set_title("Exposure Over Time — Data Unavailable", color=theme.TEXT, pad=10)

    # ── Bottom-left: Open positions over time ─────────────────────────────────
    if has_open_positions and df["open_positions"].sum() > 0:
        ax_pos.fill_between(
            df.index, df["open_positions"], color=theme.ACCENT, alpha=0.5, step="mid"
        )
        ax_pos.plot(
            df.index,
            df["open_positions"],
            color=theme.ACCENT,
            linewidth=1.2,
            drawstyle="steps-mid",
        )
        ax_pos.set_ylabel("Open Positions", color=theme.TEXT)
        ax_pos.set_title("Open Positions Over Time", color=theme.TEXT, pad=8, fontsize=11)
        _fmt_xaxis(ax_pos)
    else:
        ax_pos.text(
            0.5,
            0.5,
            "Open Positions\n\n0 throughout\n(no real fills / synthetic run)",
            ha="center",
            va="center",
            transform=ax_pos.transAxes,
            fontsize=10,
            color=theme.TEXT2,
            style="italic",
        )
        ax_pos.set_title("Open Positions Over Time", color=theme.TEXT, pad=8, fontsize=11)

    # ── Bottom-right: End-of-run exposure snapshot ────────────────────────────
    ax_snap.axis("off")

    if has_end_exposure:
        try:
            gross = float(gross_exposure or 0)
            net = float(net_exposure or 0)
        except (TypeError, ValueError):
            gross = net = 0.0

        rows = [
            ["Field", "Value"],
            ["Gross Exposure ($)", f"${gross:,.2f}"],
            ["Net Exposure ($)", f"${net:,.2f}"],
            ["Gross / Capital", f"{gross / starting_cash:.2%}" if starting_cash > 0 else "N/A"],
            ["Net / Capital", f"{net / starting_cash:.2%}" if starting_cash > 0 else "N/A"],
            ["Open Positions", str(data.portfolio_summary.get("open_positions", 0) or 0)],
            ["Data scope", "End-of-run snapshot only"],
        ]
    else:
        rows = [
            ["Field", "Status"],
            ["Gross exposure", "Not available"],
            ["Net exposure", "Not available"],
            ["Per-symbol weights", "Not available"],
            ["Allocation history", "Not available"],
            ["What's needed", "Per-tick holdings or\nstrategy allocation records"],
        ]

    tbl = ax_snap.table(
        cellText=rows[1:],
        colLabels=rows[0],
        cellLoc="left",
        loc="center",
        bbox=[0.05, 0.1, 0.9, 0.85],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)

    for col in range(2):
        h = tbl[0, col]
        h.set_facecolor(theme.SURFACE2)
        h.set_text_props(color=theme.TEXT, fontweight="bold")

    for row_i in range(1, len(rows)):
        bg = theme.SURFACE if row_i % 2 == 0 else theme.BG
        for col in range(2):
            cell = tbl[row_i, col]
            cell.set_facecolor(bg)
            cell.set_text_props(color=theme.TEXT if has_end_exposure else theme.TEXT2)

    ax_snap.set_title(
        "Exposure Snapshot (end-of-run)" if has_end_exposure else "Exposure Data Availability",
        color=theme.TEXT,
        pad=8,
        fontsize=11,
    )

    # ── Figure-level titles ───────────────────────────────────────────────────
    fig.suptitle(
        "Portfolio Exposure & Allocation",
        color=theme.TEXT,
        fontsize=14,
        y=0.99,
    )
    theme.subtitle(
        ax_cash,
        f"{data.start_date} → {data.end_date}  ·  "
        f"{'[synthetic — no real fills]' if data.is_synthetic else '[real fill data]'}  ·  "
        "Per-symbol weight history not available in this artifact.",
    )

    footnote = (
        "Per-symbol allocation history requires strategy-level position records not present in this artifact. "
        "Gross/net exposure shown is the end-of-run risk snapshot only."
    )
    fig.text(
        0.5,
        0.01,
        footnote,
        ha="center",
        va="bottom",
        fontsize=7.5,
        color=theme.TEXT2,
        style="italic",
    )

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0.04, 1, 0.98])

    out = out_dir / "12_exposure_allocation.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def _fmt_xaxis(ax: plt.Axes) -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
