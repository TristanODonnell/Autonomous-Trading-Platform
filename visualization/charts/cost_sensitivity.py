"""
Chart 10 — Net/Gross Cost Sensitivity

Tells the story: how sensitive are results to cost and slippage assumptions?

Shows how estimated total return and final value change as we add incremental
slippage / transaction cost assumptions on top of the base case.

Estimation approach:
  - Base return is taken from synthetic_stats (or real fills if present)
  - Turnover is estimated from rebalance_frequency + symbol count because
    actual trade counts (total_fills = 0 in synthetic runs) are unavailable
  - Cost drag = extra_bps × estimated_annual_turnover_fraction / 10_000
  - Results are clearly labeled as rough sensitivity estimates

If real fill data is present, actual trade count is used for turnover.

This chart does NOT claim precision. The purpose is to show whether the
result is robust (survives moderate cost increases) or fragile (breaks at
small additional costs).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from visualization import theme
from visualization.loader import ArtifactData
from visualization.metrics import compute_cagr

# ---------------------------------------------------------------------------
# Turnover estimation
# ---------------------------------------------------------------------------


def _estimate_annual_turnover(data: ArtifactData) -> tuple[float, str]:
    """
    Estimate annual portfolio turnover fraction (1.0 = full portfolio traded once/yr).

    Returns (turnover, note) where note explains the estimation method.
    """
    n_fills = data.total_fills
    n_days = getattr(data, "synthetic_stats", {}).get("n_trading_days", data.ticks_ok) or 252
    n_syms = len(data.symbols) or 1
    starting_cash = getattr(data, "starting_cash", 100_000.0)

    if n_fills > 0:
        # Real fills — compute actual turnover from fill count × avg trade size
        # Approximate: assume each fill is ~(1/n_syms) of the portfolio
        avg_trade_size = starting_cash / n_syms
        total_traded = n_fills * avg_trade_size
        turnover = total_traded / starting_cash / (n_days / 252)
        return turnover, f"estimated from {n_fills} real fills"

    # Synthetic / no fills — estimate from rebalance frequency
    settings = data.settings_summary or {}
    rebal_freq = (settings.get("rebalance_frequency") or "daily").lower()

    rebal_map = {
        "daily": 252,
        "weekly": 52,
        "monthly": 12,
    }
    rebal_per_year = rebal_map.get(rebal_freq, 252)

    # Each rebalance: assume ~20% portfolio churn (rough rule of thumb for
    # multi-symbol momentum/mean-reversion strategies)
    churn_per_rebal = 0.20
    turnover = rebal_per_year * churn_per_rebal
    note = (
        f"rough estimate: {rebal_freq} rebalance × ~{churn_per_rebal * 100:.0f}% churn/rebal "
        f"(no real fills in artifact)"
    )
    return min(turnover, 20.0), note  # cap at 20x


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    stats = getattr(data, "synthetic_stats", {})
    starting_cash = getattr(data, "starting_cash", 100_000.0)
    base_return_pct = stats.get("total_return_pct", 0.0) or 0.0
    n_days = stats.get("n_trading_days", data.ticks_ok) or 252
    base_sharpe = stats.get("sharpe_ratio", None)
    base_max_dd = stats.get("max_drawdown_pct", None)

    turnover, turnover_note = _estimate_annual_turnover(data)
    years = n_days / 252

    # ── Scenarios ──────────────────────────────────────────────────────────────
    scenarios = [
        (0, "Base case", "artifact as-is"),
        (5, "+5 bps additional cost", "low additional friction"),
        (10, "+10 bps additional cost", "moderate additional friction"),
        (25, "+25 bps additional cost", "elevated friction"),
        (50, "+50 bps stress cost", "stress scenario"),
    ]

    rows = []
    for extra_bps, scenario_name, scenario_note in scenarios:
        # Annual drag fraction from extra slippage:
        #   drag_per_year = (extra_bps / 10_000) × turnover
        # Over `years` years:
        #   total_drag_fraction = (1 - drag_per_year)^years - 1  [approximate]
        drag_per_year = (extra_bps / 10_000) * turnover
        total_drag_frac = (1 - drag_per_year) ** years - 1  # fraction, e.g. -0.03

        adj_return_pct = base_return_pct + total_drag_frac * 100
        adj_final = starting_cash * (1 + adj_return_pct / 100)
        adj_cagr = compute_cagr(starting_cash, adj_final, n_days) * 100

        # Sharpe adjustment: drag reduces mean return, vol unchanged
        if base_sharpe is not None:
            rfr_pct = stats.get("risk_free_rate_pct", 5.3) or 5.3
            vol_pct = stats.get("volatility_pct", 14.0) or 14.0
            ann_ret_adj = base_return_pct / 100 / years + total_drag_frac / years
            adj_sharpe = round((ann_ret_adj * 100 - rfr_pct) / vol_pct, 3) if vol_pct > 0 else None
        else:
            adj_sharpe = None

        rows.append(
            {
                "scenario": scenario_name,
                "note": scenario_note,
                "extra_bps": extra_bps,
                "return_pct": round(adj_return_pct, 2),
                "cagr_pct": round(adj_cagr, 2),
                "final_value": round(adj_final, 2),
                "sharpe": adj_sharpe,
                "max_dd_pct": base_max_dd,  # drag doesn't change drawdown in this simple model
            }
        )

    # ── Figure ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 7))
    fig.patch.set_facecolor(theme.BG)
    gs = fig.add_gridspec(1, 2, width_ratios=[3, 2], wspace=0.08)
    ax_bars = fig.add_subplot(gs[0])
    ax_table = fig.add_subplot(gs[1])

    # ── Bar chart: final value by scenario ───────────────────────────────────
    scenario_labels = [r["scenario"] for r in rows]
    final_values = [r["final_value"] for r in rows]
    bar_colors = [
        theme.ACCENT
        if i == 0
        else (
            theme.YELLOW
            if r["extra_bps"] <= 10
            else theme.ORANGE
            if r["extra_bps"] <= 25
            else theme.RED
        )
        for i, r in enumerate(rows)
    ]

    y_pos = list(range(len(rows)))[::-1]
    ax_bars.barh(
        y_pos, final_values, color=bar_colors, height=0.55, edgecolor=theme.BORDER, alpha=0.85
    )

    ax_bars.axvline(
        starting_cash,
        color=theme.BORDER,
        linewidth=1.0,
        linestyle="--",
        alpha=0.7,
        label=f"Starting capital ${starting_cash:,.0f}",
    )

    for y, val, row in zip(y_pos, final_values, rows, strict=False):
        ax_bars.text(
            val + starting_cash * 0.003,
            y,
            f"${val:,.0f}  ({row['return_pct']:+.1f}%)",
            va="center",
            fontsize=8.5,
            color=theme.TEXT,
        )

    ax_bars.set_yticks(y_pos)
    ax_bars.set_yticklabels(scenario_labels, fontsize=9)
    ax_bars.set_xlabel("Estimated Final Portfolio Value ($)", color=theme.TEXT2, fontsize=10)
    ax_bars.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax_bars.set_title("Cost Sensitivity — Final Value", color=theme.TEXT, pad=10, fontsize=12)

    # Highlight the breakeven line (starting_cash) with a label
    ax_bars.text(
        starting_cash,
        y_pos[-1] - 0.45,
        f" Break-even\n ${starting_cash:,.0f}",
        va="bottom",
        ha="left",
        fontsize=7.5,
        color=theme.TEXT2,
        style="italic",
    )

    # ── Table: full scenario breakdown ────────────────────────────────────────
    col_headers = ["Scenario", "+Bps", "Total Ret.", "CAGR", "Final Value", "Sharpe", "Max DD"]
    table_data = []
    for r in rows:
        sharpe_str = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "—"
        max_dd_str = f"{r['max_dd_pct']:.1f}%" if r["max_dd_pct"] is not None else "—"
        table_data.append(
            [
                r["scenario"],
                f"+{r['extra_bps']}",
                f"{r['return_pct']:+.1f}%",
                f"{r['cagr_pct']:+.1f}%",
                f"${r['final_value']:,.0f}",
                sharpe_str,
                max_dd_str,
            ]
        )

    ax_table.axis("off")
    tbl = ax_table.table(
        cellText=table_data,
        colLabels=col_headers,
        cellLoc="center",
        loc="center",
        bbox=[0, 0, 1, 1],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8.5)

    for col in range(len(col_headers)):
        cell = tbl[0, col]
        cell.set_facecolor(theme.SURFACE2)
        cell.set_text_props(color=theme.TEXT, fontweight="bold")

    for row_idx, _r in enumerate(rows, start=1):
        color = bar_colors[row_idx - 1]
        bg = theme.SURFACE if row_idx % 2 == 0 else theme.BG
        for col in range(len(col_headers)):
            cell = tbl[row_idx, col]
            cell.set_facecolor(bg)
            cell.set_text_props(color=color if col in (2, 3, 4) else theme.TEXT)

    # ── Titles & disclaimers ──────────────────────────────────────────────────
    fig.suptitle(
        "Cost & Slippage Sensitivity Analysis",
        color=theme.TEXT,
        fontsize=14,
        y=0.98,
    )
    theme.subtitle(
        ax_bars,
        f"{data.start_date} → {data.end_date}  ·  "
        f"Turnover assumption: {turnover:.1f}× annual  ·  {turnover_note}",
    )

    footnote = (
        "ROUGH SENSITIVITY ESTIMATE — actual trade turnover is not recorded in this artifact. "
        f"Turnover estimated as {turnover:.1f}× annual ({turnover_note}). "
        "Re-run with real fill data for precise cost impact."
    )
    fig.text(
        0.5,
        0.01,
        footnote,
        ha="center",
        va="bottom",
        fontsize=7,
        color=theme.TEXT2,
        style="italic",
        wrap=True,
    )

    theme.watermark(fig)
    plt.tight_layout(rect=[0, 0.06, 1, 0.97])

    out = out_dir / "10_cost_sensitivity.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
