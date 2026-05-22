from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RegimeTransition:
    transition_index: int
    timestamp: datetime | None
    from_regime: str
    to_regime: str
    duration_before: int


@dataclass(frozen=True)
class TransitionWindow:
    transition: RegimeTransition
    pre_return: float | None
    post_return: float | None
    pre_max_drawdown: float | None
    post_max_drawdown: float | None


@dataclass(frozen=True)
class RegimeDurationStats:
    label: str
    episode_count: int
    mean_duration: float
    median_duration: float
    max_duration: int
    min_duration: int


@dataclass(frozen=True)
class RegimeTransitionAnalysis:
    dimension: str
    transition_count: int
    transitions: list[RegimeTransition]
    transition_matrix: dict[str, dict[str, int]]
    duration_stats: dict[str, RegimeDurationStats]
    transition_windows: list[TransitionWindow]
    avg_pre_transition_return: float | None
    avg_post_transition_return: float | None


def _compute_window_stats(
    bar_returns: np.ndarray,
    idx: int,
    window_bars: int,
) -> tuple[float | None, float | None, float | None, float | None]:
    n = len(bar_returns)

    pre_start = max(0, idx - window_bars)
    pre_end = idx
    post_start = idx
    post_end = min(n, idx + window_bars)

    pre_slice = bar_returns[pre_start:pre_end]
    post_slice = bar_returns[post_start:post_end]

    def _safe_mean(arr: np.ndarray) -> float | None:
        return float(np.mean(arr)) if len(arr) > 0 else None

    def _safe_mdd(arr: np.ndarray) -> float | None:
        if len(arr) == 0:
            return None
        eq = np.cumprod(1.0 + arr)
        peak = np.maximum.accumulate(eq)
        with np.errstate(divide="ignore", invalid="ignore"):
            dd = np.where(peak == 0.0, 0.0, (eq - peak) / peak)
        return float(np.min(dd))

    return (
        _safe_mean(pre_slice),
        _safe_mean(post_slice),
        _safe_mdd(pre_slice),
        _safe_mdd(post_slice),
    )


class RegimeTransitionAnalyzer:
    def analyze(
        self,
        *,
        equity_curve_with_regime: pd.DataFrame,
        bar_returns_with_regime: pd.DataFrame,
        dimension: str,
        window_bars: int = 20,
    ) -> RegimeTransitionAnalysis:
        col = f"regime_{dimension}"

        if col not in equity_curve_with_regime.columns:
            return RegimeTransitionAnalysis(
                dimension=dimension,
                transition_count=0,
                transitions=[],
                transition_matrix={},
                duration_stats={},
                transition_windows=[],
                avg_pre_transition_return=None,
                avg_post_transition_return=None,
            )

        sorted_ec = equity_curve_with_regime.sort_values("timestamp").reset_index(drop=True)
        labels = sorted_ec[col].tolist()
        timestamps = sorted_ec["timestamp"].tolist()

        transitions: list[RegimeTransition] = []
        matrix: dict[str, dict[str, int]] = {}
        episode_durations: dict[str, list[int]] = {}

        current_label = labels[0] if labels else None
        current_start = 0

        for i in range(1, len(labels)):
            lbl = labels[i]
            if lbl != current_label:
                duration = i - current_start
                if current_label is not None:
                    episode_durations.setdefault(str(current_label), []).append(duration)
                    if lbl is not None:
                        from_str = str(current_label)
                        to_str = str(lbl)
                        transitions.append(
                            RegimeTransition(
                                transition_index=len(transitions),
                                timestamp=timestamps[i] if i < len(timestamps) else None,
                                from_regime=from_str,
                                to_regime=to_str,
                                duration_before=duration,
                            )
                        )
                        matrix.setdefault(from_str, {})
                        matrix[from_str][to_str] = matrix[from_str].get(to_str, 0) + 1
                current_label = lbl
                current_start = i

        # Close the last episode
        if current_label is not None:
            duration = len(labels) - current_start
            episode_durations.setdefault(str(current_label), []).append(duration)

        duration_stats: dict[str, RegimeDurationStats] = {}
        for lbl_str, durations in episode_durations.items():
            if durations:
                duration_stats[lbl_str] = RegimeDurationStats(
                    label=lbl_str,
                    episode_count=len(durations),
                    mean_duration=float(np.mean(durations)),
                    median_duration=float(median(durations)),
                    max_duration=max(durations),
                    min_duration=min(durations),
                )

        # Build bar returns array for window analysis
        sorted_br = bar_returns_with_regime.sort_values("timestamp").reset_index(drop=True)
        br_returns = sorted_br["bar_return"].to_numpy(dtype=np.float64)
        br_timestamps = sorted_br["timestamp"].tolist()

        transition_windows: list[TransitionWindow] = []
        for t in transitions:
            # Find index of transition timestamp in bar_returns frame
            idx = None
            if t.timestamp is not None:
                matches = [j for j, ts in enumerate(br_timestamps) if ts == t.timestamp]
                idx = matches[0] if matches else None

            if idx is not None:
                pre_ret, post_ret, pre_mdd, post_mdd = _compute_window_stats(
                    br_returns, idx, window_bars
                )
            else:
                pre_ret, post_ret, pre_mdd, post_mdd = None, None, None, None

            transition_windows.append(
                TransitionWindow(
                    transition=t,
                    pre_return=pre_ret,
                    post_return=post_ret,
                    pre_max_drawdown=pre_mdd,
                    post_max_drawdown=post_mdd,
                )
            )

        pre_returns = [w.pre_return for w in transition_windows if w.pre_return is not None]
        post_returns = [w.post_return for w in transition_windows if w.post_return is not None]

        return RegimeTransitionAnalysis(
            dimension=dimension,
            transition_count=len(transitions),
            transitions=transitions,
            transition_matrix=matrix,
            duration_stats=duration_stats,
            transition_windows=transition_windows,
            avg_pre_transition_return=float(np.mean(pre_returns)) if pre_returns else None,
            avg_post_transition_return=float(np.mean(post_returns)) if post_returns else None,
        )
