"""
Shared visual theme for platform backtest storytelling charts.

Colors mirror the frontend design tokens so charts feel native to the platform.
All charts use a dark background suitable for embedding in dark-themed slides.
"""

from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns

# ---------------------------------------------------------------------------
# Platform design token palette
# ---------------------------------------------------------------------------

BG = "#070B0F"
SURFACE = "#0D1117"
SURFACE2 = "#111820"
BORDER = "#1C2532"
TEXT = "#C9D1D9"
TEXT2 = "#8B949E"

ACCENT = "#00E5A0"  # green — positive / platform line
RED = "#FF4D6D"  # red — negative / loss / risk
YELLOW = "#E8A838"  # yellow — warning
BLUE = "#3B9EFF"  # blue — benchmark / info
PURPLE = "#9B72FF"  # purple — secondary metric
ORANGE = "#FF8C42"  # orange — probation

# Drawdown ladder state colors
LADDER_COLORS = {
    "normal": ACCENT,
    "warning": YELLOW,
    "probation": ORANGE,
    "suspended": RED,
    "breached": "#8B0000",
}

# Governance event colors
EVENT_COLORS = {
    "governance_manual_transition": PURPLE,
    "governance_auto_promotion": ACCENT,
    "governance_auto_demotion": RED,
    "settings_changed": BLUE,
    "settings_seeded": BLUE,
    "allocation_override_set": YELLOW,
    "allocation_override_cleared": YELLOW,
    "controls_paused": ORANGE,
    "controls_resumed": ACCENT,
    "strategy_disabled": RED,
    "strategy_enabled": ACCENT,
    "trading_disabled": RED,
    "trading_enabled": ACCENT,
    "safety_emergency_halt": "#FF0000",
    "safety_release_halt": ACCENT,
    "trading_mode_changed": PURPLE,
    "health_review_acknowledged": TEXT2,
}

EVENT_LABELS = {
    "governance_manual_transition": "Gov Transition",
    "governance_auto_promotion": "Auto Promotion",
    "governance_auto_demotion": "Auto Demotion",
    "settings_changed": "Settings Change",
    "allocation_override_set": "Alloc Override",
    "allocation_override_cleared": "Override Cleared",
    "controls_paused": "Paused",
    "controls_resumed": "Resumed",
    "strategy_disabled": "Strategy Off",
    "strategy_enabled": "Strategy On",
    "safety_emergency_halt": "Emergency Halt",
    "safety_release_halt": "Halt Released",
    "trading_mode_changed": "Mode Changed",
    "health_review_acknowledged": "Health Review",
}

# ---------------------------------------------------------------------------
# Figure sizes (16:9 aspect, good for slides)
# ---------------------------------------------------------------------------

FIGSIZE_WIDE = (16, 7)  # full-width slide chart
FIGSIZE_SQUARE = (10, 9)  # square / nearly square
FIGSIZE_TALL = (14, 9)  # tall chart (monthly heatmap, tables)
FIGSIZE_HALF = (9, 6)  # half-width panel

DPI = 150  # balance between quality and file size; use 300 for print


# ---------------------------------------------------------------------------
# Apply theme globally
# ---------------------------------------------------------------------------


def apply() -> None:
    """Apply the dark platform theme to all subsequent matplotlib figures."""
    mpl.rcParams.update(
        {
            "figure.facecolor": BG,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": BORDER,
            "axes.labelcolor": TEXT,
            "axes.titlecolor": TEXT,
            "axes.titlesize": 14,
            "axes.labelsize": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": BORDER,
            "grid.linewidth": 0.6,
            "grid.linestyle": "--",
            "xtick.color": TEXT2,
            "ytick.color": TEXT2,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.facecolor": SURFACE2,
            "legend.edgecolor": BORDER,
            "legend.labelcolor": TEXT,
            "legend.fontsize": 9,
            "text.color": TEXT,
            "font.family": "DejaVu Sans",
            "lines.linewidth": 1.8,
            "patch.edgecolor": BORDER,
            "savefig.facecolor": BG,
            "savefig.bbox": "tight",
            "savefig.dpi": DPI,
        }
    )

    sns.set_theme(
        style="dark",
        rc={
            "figure.facecolor": BG,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": BORDER,
            "axes.labelcolor": TEXT,
            "grid.color": BORDER,
            "text.color": TEXT,
            "xtick.color": TEXT2,
            "ytick.color": TEXT2,
        },
    )


def subtitle(ax: plt.Axes, text: str) -> None:
    """Add a muted subtitle below the main axis title."""
    ax.annotate(
        text,
        xy=(0, 1.01),
        xycoords="axes fraction",
        fontsize=9,
        color=TEXT2,
        ha="left",
    )


def watermark(fig: plt.Figure) -> None:
    """Add a subtle platform watermark to the figure."""
    fig.text(
        0.99,
        0.01,
        "Autonomous Trading Platform",
        ha="right",
        va="bottom",
        fontsize=7,
        color=BORDER,
        style="italic",
    )
