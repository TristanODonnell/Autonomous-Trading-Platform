"""
Point-in-time index membership (survivorship-safe candidate lists).

Alpaca's asset list only describes today's market: delisted tickers are missing
from ``get_all_assets`` (e.g. ATVI, PXD) or not found at all (SIVB, FRC), even
though Alpaca still serves their historical bars. Historical index membership
fills that gap: "who was in the S&P 500 on 2023-01-03" includes SIVB and FRC,
whose bars can then be fetched from Alpaca by ticker.

Membership is keyed by the ticker used *during* each spell (FB until
2022-06-09, META after), which is also the ticker Alpaca serves historical bars
under for that period.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib import resources

_SP500_FILE = "sp500_ticker_start_end.csv"


@dataclass(frozen=True)
class MembershipSpell:
    symbol: str
    start: date
    end: date | None  # None = still a member

    def active_on(self, as_of: date) -> bool:
        return self.start <= as_of and (self.end is None or as_of < self.end)


class IndexConstituents:
    """Membership spells for one index, queried as of a date."""

    def __init__(self, *, index_name: str, spells: list[MembershipSpell]) -> None:
        self.index_name = index_name
        self._spells = spells

    def members_as_of(self, as_of: date) -> list[str]:
        """Sorted tickers that were index members on ``as_of``.

        A spell's end_date is the day the ticker left the index, so it is
        excluded on that date (and the replacement, starting that day, included).
        """
        return sorted({s.symbol for s in self._spells if s.active_on(as_of)})

    def was_member(self, symbol: str, as_of: date) -> bool:
        return any(s.symbol == symbol and s.active_on(as_of) for s in self._spells)

    def __len__(self) -> int:
        return len(self._spells)


def parse_membership_csv(lines: list[str], *, index_name: str) -> IndexConstituents:
    """Parse ``ticker,start_date,end_date`` rows (end_date blank = current member)."""
    spells: list[MembershipSpell] = []
    for row in csv.DictReader(lines):
        symbol = (row.get("ticker") or "").strip().upper()
        start = (row.get("start_date") or "").strip()
        if not symbol or not start:
            continue
        end = (row.get("end_date") or "").strip()
        spells.append(
            MembershipSpell(
                symbol=symbol,
                start=date.fromisoformat(start),
                end=date.fromisoformat(end) if end else None,
            )
        )
    if not spells:
        raise ValueError(f"No membership rows parsed for index {index_name!r}")
    return IndexConstituents(index_name=index_name, spells=spells)


@lru_cache(maxsize=1)
def load_sp500_constituents() -> IndexConstituents:
    """Bundled point-in-time S&P 500 membership (see universe/reference_data)."""
    text = (
        resources.files("autonomous_trading_platform.universe.reference_data")
        .joinpath(_SP500_FILE)
        .read_text(encoding="utf-8")
    )
    return parse_membership_csv(text.splitlines(), index_name="sp500")
