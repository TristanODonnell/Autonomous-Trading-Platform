from __future__ import annotations


def average_volume(
    volumes: list[float],
    window: int,
) -> float | None:
    if window <= 0:
        raise ValueError("window must be positive")

    if len(volumes) < window:
        return None

    subset = volumes[-window:]
    return sum(subset) / window


def volume_ratio(
    volumes: list[float],
    window: int,
) -> float | None:
    avg = average_volume(volumes, window)

    if avg is None or avg == 0:
        return None

    return volumes[-1] / avg


def volume_spike(
    volumes: list[float],
    window: int,
    threshold: float = 2.0,
) -> bool:
    ratio = volume_ratio(volumes, window)

    if ratio is None:
        return False

    return ratio >= threshold
