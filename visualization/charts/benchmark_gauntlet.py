"""
Chart 9 — Benchmark Gauntlet

Tells the story: did the platform beat basic alternatives?

Compares the platform against every available baseline:
  - Cash / risk-free return
  - Synthetic SPY benchmark (correlated GBM)
  - Target return hurdles: 15%, 20%, 25%  (NOT financial advisor standards)
  - External references (actual SPY/QQQ/VTI): shown as "unavailable" unless
    external price data is provided via config

Baselines NOT computable from this artifact (no per-symbol price history):
  - Equal-weight same-universe buy-and-hold
  - Monthly-rebalance same-universe baseline
  - Simple momentum baseline

If a baseline is unavailable, the row is shown with a clear "N/A" label.
No fake benchmark returns are generated.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from visualization import theme
from visualization.loader import ArtifactData
from visualization.metrics import hurdle_final_value

# External / reference benchmarks that would require real price data.
# Shown as "unavailable" unless injected via artifact config / metadata.
_EXTERNAL_REFS = [
    ("S&P 500 / SPY", "Actual index return", None),
    ("Nasdaq-100 / QQQ", "Actual index return", None),
    ("VTI (US Total Mkt)", "Actual index return", None),
    ("60/40 Portfolio", "Blended equity/bond", None),
]

# Same-universe baselines that require per-symbol OHLCV history
_UNIVERSE_BASELINES = [
    "Equal-weight buy-and-hold",
    "Monthly-rebalance same-universe",
    "Simple momentum (same universe)",
]


def render(data: ArtifactData, out_dir: Path) -> Path:
    theme.apply()

    stats = getattr(data, "synthetic_stats", {})
    starting_cash = getattr(data, "starting_cash", 100_000.0)

    # ── Collect available baselines ───────────────────────────────────────────
    platform_final = stats.get("final_value", starting_cash)
    platform_return = stats.get("total_return_pct", 0.0) or 0.0

    bench_final = stats.get("final_value_bench", None)
    rfr_pct = stats.get("risk_free_rate_pct", 5.3) or 5.3
    n_days = stats.get("n_trading_days", 252) or 252
    years = n_days / 252
    cash_final_val = starting_cash * ((1 + rfr_pct / 100) ** years)

    bench_return_pct = stats.get("total_return_pct_bench", None)
    bench_final_val = bench_final  # already a $ value from synthetic_stats

    # ── Build rows ────────────────────────────────────────────────────────────
    # Each row: (label, final_value, total_return_pct, note, color, available)
    rows = []

    rows.append(
        (
            "Platform",
            platform_final,
            platform_return,
            "synthetic" if data.is_synthetic else "live/paper",
            theme.ACCENT,
            True,
        )
    )

    if bench_final_val is not None:
        rows.append(
            (
                "SPY Benchmark (synthetic proxy)",
                bench_final_val,
                bench_return_pct,
                "synthetic benchmark",
                theme.BLUE,
                True,
            )
        )

    rows.append(
        (
            "Cash / Risk-Free",
            cash_final_val,
            round((cash_final_val / starting_cash - 1) * 100, 2),
            f"{rfr_pct:.1f}% annual, compounded",
            theme.TEXT2,
            True,
        )
    )

    for hurdle_pct, hcolor in [(15, theme.YELLOW), (20, theme.ORANGE), (25, theme.RED)]:
        hval = hurdle_final_value(starting_cash, hurdle_pct)
        rows.append(
            (
                f"{hurdle_pct}% Target Return Hurdle",
                hval,
                float(hurdle_pct),
                "target hurdle benchmark",
                hcolor,
                True,
            )
        )

    for label in _UNIVERSE_BASELINES:
        rows.append((label, None, None, "requires per-symbol price history", theme.BORDER, False))

    for label, desc, val in _EXTERNAL_REFS:
        rows.append((label, val, None, f"external ref — {desc} — not loaded", theme.BORDER, False))

    # ── Layout ────────────────────────────────────────────────────────────────
    fig, (ax_bars, ax_table) = plt.subplots(
        1,
        2,
        figsize=(16, max(7, len(rows) * 0.65 + 2)),
        gridspec_kw={"width_ratios": [3, 2]},
    )
    fig.patch.set_facecolor(theme.BG)

    # ── Left: horizontal bar chart ────────────────────────────────────────────
    labels = [r[0] for r in rows]
    finals = [r[1] if r[5] else 0 for r in rows]
    colors = [r[4] for r in rows]
    available = [r[5] for r in rows]

    y_pos = list(range(len(labels)))[::-1]  # top-to-bottom ordering

    for _i, (y, val, color, avail) in enumerate(
        zip(y_pos, finals, colors, available, strict=False)
    ):
        if avail and val and val > 0:
            ax_bars.barh(y, val, color=color, alpha=0.8, height=0.55, edgecolor=theme.BORDER)
            ax_bars.text(
                val + starting_cash * 0.005,
                y,
                f"${val:,.0f}",
                va="center",
                ha="left",
                fontsize=8.5,
                color=color,
            )
        else:
            ax_bars.barh(
                y,
                starting_cash * 0.05,
                color=theme.BORDER,
                alpha=0.3,
                height=0.55,
                edgecolor=theme.BORDER,
            )
            ax_bars.text(
                starting_cash * 0.06,
                y,
                "Unavailable",
                va="center",
                ha="left",
                fontsize=8,
                color=theme.TEXT2,
                style="italic",
            )

    ax_bars.axvline(
        starting_cash,
        color=theme.BORDER,
        linewidth=1.0,
        linestyle="--",
        alpha=0.8,
        label=f"Starting capital ${starting_cash:,.0f}",
    )

    # Platform reference line
    ax_bars.axvline(platform_final, color=theme.ACCENT, linewidth=0.8, linestyle=":", alpha=0.6)

    ax_bars.set_yticks(y_pos)
    ax_bars.set_yticklabels(labels, fontsize=9)
    ax_bars.set_xlabel("Final Portfolio Value ($)", color=theme.TEXT2, fontsize=10)
    ax_bars.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax_bars.set_title("Final Value vs Starting Capital", color=theme.TEXT, pad=10, fontsize=12)

    # ── Right: numeric summary table ─────────────────────────────────────────
    col_headers = ["Benchmark / Hurdle", "Total Return", "Final Value ($)", "vs Platform", "Notes"]
    table_rows = []
    for label, final, ret, note, _color, avail in rows:
        if avail and final is not None:
            ret_str = f"{ret:+.1f}%" if ret is not None else "—"
            final_str = f"${final:,.0f}"
            diff = final - platform_final
            diff_str = f"{diff:+,.0f}" if diff != 0 else "baseline"
        else:
            ret_str = "N/A"
            final_str = "N/A"
            diff_str = "N/A"
        table_rows.append([label, ret_str, final_str, diff_str, note])

    ax_table.axis("off")
    tbl = ax_table.table(
        cellText=table_rows,
        colLabels=col_headers,
        cellLoc="left",
        loc="center",
        bbox=[0, 0, 1, 1],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)

    # Style header row
    for col in range(len(col_headers)):
        cell = tbl[0, col]
        cell.set_facecolor(theme.SURFACE2)
        cell.set_text_props(color=theme.TEXT, fontweight="bold")

    # Style data rows
    for row_idx, (_, _final, _, _, _, avail) in enumerate(rows, start=1):
        row_color = theme.SURFACE if row_idx % 2 == 0 else theme.BG
        for col in range(len(col_headers)):
            cell = tbl[row_idx, col]
            cell.set_facecolor(row_color)
            fc = rows[row_idx - 1][4]
            cell.set_text_props(color=fc if avail else theme.TEXT2)

    # ── Titles & notes ────────────────────────────────────────────────────────
    fig.suptitle(
        "Benchmark Gauntlet — Platform vs Available Baselines",
        color=theme.TEXT,
        fontsize=14,
        y=0.98,
    )
    theme.subtitle(
        ax_bars,
        f"{data.start_date} → {data.end_date}  ·  Starting capital: ${starting_cash:,.0f}  ·  "
        f"{'[synthetic financial data]' if data.is_synthetic else '[live data]'}  ·  "
        f"Target hurdles are business benchmarks, not advisory standards.",
    )

    footnote = (
        "External references (SPY, QQQ, VTI, 60/40) require real price data and are not loaded.\n"
        "Same-universe baselines require per-symbol OHLCV history not present in this artifact.\n"
        "Target hurdles = starting_cash × (1 + hurdle%). Not financial advisory standards."
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
    plt.tight_layout(rect=[0, 0.06, 1, 0.97])

    out = out_dir / "09_benchmark_gauntlet.png"
    fig.savefig(out, dpi=theme.DPI, bbox_inches="tight")
    plt.close(fig)
    return out
