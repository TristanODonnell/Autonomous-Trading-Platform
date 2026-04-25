from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_trading_platform.contracts.common.enums import SignalDirection


def build_signal_id(
    *,
    run_id: UUID,
    strategy_id: str,
    symbol: str,
    bar_timestamp: Any,
    direction: SignalDirection,
    params: dict[str, Any],
) -> UUID:
    seed = (
        f"run_id={run_id}|"
        f"strategy_id={strategy_id}|"
        f"symbol={symbol}|"
        f"bar_timestamp={bar_timestamp.isoformat()}|"
        f"direction={direction.value}|"
        f"params={sorted(params.items())}"
    )
    return uuid5(NAMESPACE_URL, seed)


def get_close(bar: Any) -> float:
    if hasattr(bar, "close"):
        return float(bar.close)

    if isinstance(bar, dict) and "close" in bar:
        return float(bar["close"])

    raise ValueError("Bar object does not expose a close price.")


def extract_closes(bars: list[Any]) -> list[float]:
    return [get_close(bar) for bar in bars]


def get_volume(bar: Any) -> float:
    if hasattr(bar, "volume"):
        return float(bar.volume)

    if isinstance(bar, dict) and "volume" in bar:
        return float(bar["volume"])

    raise ValueError("Bar object does not expose volume.")


def extract_volumes(bars: list[Any]) -> list[float]:
    return [get_volume(bar) for bar in bars]
