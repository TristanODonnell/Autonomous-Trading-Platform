"""Position sizing rules shared by research simulations and the trading cycle (step 5c-E).

Research re-sims (admission, bench, rotation evidence) and the platform portfolio cycle
must size the same signal identically, or they measure different strategies. Both size
a BUY as::

    notional = sleeve budget / symbols the strategy trades     (equal split)
             * vol scalar from the last VOL_LOOKBACK_BARS closes  (only scales down)
    capped at the per-symbol exposure cap, then floored to whole shares.

The platform applies its account-level layers on top (drawdown taper, policy position
cap, sleeve-budget and cross-sleeve symbol trims); a re-sim cannot see those.
"""

from __future__ import annotations

from decimal import Decimal

# Closes before the decision bar that feed the volatility scalar, in both paths.
VOL_LOOKBACK_BARS = 20

# Recorded in research cache keys: results sized under another rule never collide.
SIZING_MODEL = "equal_split_vol_scaled_v1"


def position_budget_usd(sleeve_budget_usd: Decimal, symbol_count: int | None) -> Decimal:
    """One position's share of the sleeve budget: an equal split across the symbols traded.

    Every symbol gets the same slice, so the sleeve can hold every position its signals
    call for, independent of which symbols signal first. ``symbol_count`` None sizes each
    position from the whole budget (legacy single-strategy mode).
    """
    if symbol_count is None:
        return sleeve_budget_usd
    if symbol_count < 1:
        raise ValueError(f"symbol_count must be >= 1, got {symbol_count}")
    return sleeve_budget_usd / Decimal(symbol_count)
