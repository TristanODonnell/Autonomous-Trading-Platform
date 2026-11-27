"""Loads what the signal screen reads and runs it (``atp research signal-screen``).

The screen itself (``research/signal_screen/``) is arithmetic on arrays. This module is
where its inputs come from, by the same rules the admission layout and buy and hold
use, so the three agree on which days and which names they are looking at:

- the bars of a registered research-window dataset;
- the names in force on an exported universe timeline, asked at each session's opening;
- the dataset's sessions (the calendar's, less the ones it left out by name);
- the selection and evaluation pieces of the admission layout.

**The holdout is not read.** Bars are asked for up to the last evaluation session, and
a bar dated on or after ``HOLDOUT_START`` in what comes back stops the run. Returns
that would end in the holdout are not measured: there is no bar to measure them to.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.accounting.corporate_actions import is_cash_dividend, is_split
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    ResearchWindowDataset,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import UniverseTimeline
from autonomous_trading_platform.research.pipeline.admission_window import (
    AdmissionWindowError,
    admission_window_layout,
)
from autonomous_trading_platform.research.pipeline.holdout import HOLDOUT_START
from autonomous_trading_platform.research.signal_screen.bars import NameBars, build_name_bars
from autonomous_trading_platform.research.signal_screen.library import Library
from autonomous_trading_platform.research.signal_screen.panel import Scale, build_panel
from autonomous_trading_platform.research.signal_screen.screen import ScreenSettings, run_screen

SCALES: tuple[Scale, ...] = ("5min", "daily")


class SignalScreenError(RuntimeError):
    """The screen cannot be run on what it was pointed at; nothing is reported."""


@dataclass(frozen=True)
class SignalScreenSource:
    """Where a screen reads from. Built by :func:`signal_screen_source` from a database
    session; a test hands its own."""

    dataset: ResearchWindowDataset
    timeline: UniverseTimeline
    # (symbol, first day, last day) -> its bars: a frame with timestamp, close, volume.
    read_bars: Callable[[str, date, date], pd.DataFrame]
    # (symbols, first day, last day) -> their corporate actions.
    actions_for: Callable[[Sequence[str], date, date], Sequence[Any]]
    # The dataset's sessions between two days, both included.
    sessions_between: Callable[[date, date], list[date]]


def signal_screen_source(
    session: Session,
    *,
    dataset_version: str,
    universe_timeline: str,
    base_path: str | Path = "data",
) -> SignalScreenSource:
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        _research_sessions,  # one rule for a dataset's sessions, shared with the layout
    )
    from autonomous_trading_platform.application.services.research_window_dataset_loader import (
        load_research_window_dataset,
    )
    from autonomous_trading_platform.application.services.universe_timeline_loader import (
        load_universe_timeline,
    )
    from autonomous_trading_platform.contracts.common.enums import PriceBasis
    from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
        ResearchDatasetResolver,
    )
    from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
    from autonomous_trading_platform.storage.sor.services.corporate_action_split_source import (
        SorCorporateActionSource,
    )

    dataset = load_research_window_dataset(session, dataset_version)
    timeline = load_universe_timeline(session, universe_timeline)
    # Raw bars: splits and dividends are applied here from the stored actions, as the
    # simulator and buy and hold apply them.
    resolved = ResearchDatasetResolver(base_path=str(base_path)).resolve_bars_dataset(
        dataset_version=dataset_version, price_basis=PriceBasis.RAW
    )
    reader = HistoricalBarDatasetReader(session=session, base_path=base_path)
    actions = SorCorporateActionSource(session)

    def read_bars(symbol: str, first: date, last: date) -> pd.DataFrame:
        table = reader.read(
            dataset=resolved.dataset,
            dataset_version=dataset_version,
            symbol=symbol,
            start_date=first,
            end_date=last,
        ).select(["timestamp", "close", "volume"])
        return table.to_pandas()

    def actions_for(symbols: Sequence[str], first: date, last: date) -> Sequence[Any]:
        return actions.actions_for(symbols=symbols, start_date=first, end_date=last)

    return SignalScreenSource(
        dataset=dataset,
        timeline=timeline,
        read_bars=read_bars,
        actions_for=actions_for,
        sessions_between=_research_sessions(dataset),
    )


def _openings(names: Sequence[NameBars], sessions: int) -> np.ndarray:
    """Each session's first bar time over every name; NaT where no name has a bar."""
    opening = np.full(sessions, np.datetime64("NaT"), dtype="datetime64[ns]")
    for name in names:
        seen, first = np.unique(name.session, return_index=True)
        times = name.timestamps[first]
        current = opening[seen]
        opening[seen] = np.where(np.isnat(current) | (times < current), times, current)
    return opening


def _in_force(timeline: UniverseTimeline, names: Sequence[NameBars], sessions: int) -> np.ndarray:
    """``[session, name]``: on the list in force at the session's opening, the moment
    buy and hold asks the timeline at."""
    table = np.zeros((sessions, len(names)), dtype=bool)
    for index, opening in enumerate(_openings(names, sessions)):
        if np.isnat(opening):
            continue
        members = timeline.members_at(pd.Timestamp(opening, tz="UTC").to_pydatetime())
        table[index] = [name.symbol in members for name in names]
    return table


def run_signal_screen(
    source: SignalScreenSource,
    *,
    library: Library,
    scales: Sequence[Scale] = SCALES,
    settings: ScreenSettings | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    dataset, timeline = source.dataset, source.timeline
    try:
        layout = admission_window_layout(
            holdout_start=HOLDOUT_START,
            as_of=dataset.last_day,
            data_first=dataset.first_day,
            data_last=dataset.last_day,
            trading_days=source.sessions_between,
        )
    except AdmissionWindowError as exc:
        raise SignalScreenError(
            f"the admission layout cannot be laid on dataset {dataset.dataset_version_id}: {exc}"
        ) from exc
    last = layout.last_gate_day
    if last >= HOLDOUT_START:
        raise SignalScreenError(f"the last day read, {last}, is not before the holdout")

    # Sessions before the selection piece only warm the indicators up.
    sessions = list(source.sessions_between(dataset.first_day, last))
    first_observed = sessions.index(layout.start)

    in_window = timeline.symbols_on_dates(layout.start, last)
    held = set(dataset.symbols)
    symbols = [symbol for symbol in in_window if symbol in held]
    without_bars = sorted(set(in_window) - held)
    actions = list(source.actions_for(symbols, dataset.first_day, last))

    names: list[NameBars] = []
    bars_not_read = 0
    for symbol in symbols:
        if progress is not None:
            progress(f"bars {symbol}")
        frame = source.read_bars(symbol, dataset.first_day, last)
        stamps = pd.DatetimeIndex(pd.to_datetime(frame["timestamp"], utc=True))
        if len(stamps) and stamps.max().date() >= HOLDOUT_START:
            raise SignalScreenError(
                f"{symbol}: a bar dated {stamps.max().date()} was read; nothing from "
                f"{HOLDOUT_START} on may be"
            )
        bars = build_name_bars(
            symbol=symbol,
            timestamps=stamps,
            close=frame["close"].to_numpy(dtype=float),
            volume=frame["volume"].to_numpy(dtype=float),
            actions=actions,
            sessions=sessions,
        )
        bars_not_read += bars.bars_not_read
        if len(bars):
            names.append(bars)
        else:
            without_bars.append(symbol)
    if not names:
        raise SignalScreenError("no name in force has bars in the window")

    in_force = _in_force(timeline, names, len(sessions))
    panels = {
        scale: build_panel(
            scale=scale,
            names=names,
            sessions=sessions,
            first_observed=first_observed,
            in_force=in_force,
        )
        for scale in scales
    }
    observed = sessions[first_observed:]
    pieces = {"selection": layout.selection, "evaluation": layout.evaluation}
    groups = {
        name: np.array([span.first <= day <= span.last for day in observed])
        for name, span in pieces.items()
    }

    report = run_screen(
        panels=panels, library=library, groups=groups, settings=settings, progress=progress
    )
    report["source"] = {
        "dataset_version": dataset.dataset_version_id,
        "universe_timeline": timeline.dataset_version_id,
        "universe_timeline_hash": timeline.content_hash,
        "first_bar_day": sessions[0].isoformat(),
        "warmup_sessions": first_observed,
        "first_session": observed[0].isoformat(),
        "last_session": observed[-1].isoformat(),
        "sessions": len(observed),
        "pieces": {
            name: {
                "first": span.first.isoformat(),
                "last": span.last.isoformat(),
                "sessions": int(groups[name].sum()),
            }
            for name, span in pieces.items()
        },
        "holdout_start": HOLDOUT_START.isoformat(),
        "names": len(names),
        "names_without_bars": sorted(without_bars),
        "bars_read": int(sum(len(name) for name in names)),
        # Bars on days that are not sessions of the dataset, or outside 09:30 to 16:00.
        "bars_not_read": int(bars_not_read),
        "corporate_actions": {
            "splits": sum(1 for action in actions if is_split(action)),
            "cash_dividends": sum(1 for action in actions if is_cash_dividend(action)),
        },
    }
    return report
