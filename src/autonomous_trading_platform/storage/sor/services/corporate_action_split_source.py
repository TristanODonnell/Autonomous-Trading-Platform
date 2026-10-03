"""Splits read from the system of record, cached per symbol for the life of the source.

Context builders call ``splits_for`` once per context build (every strategy × symbol ×
cycle), so the SOR is queried once per symbol and the answer reused; the cache covers
the widest window asked so far for that symbol.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from autonomous_trading_platform.accounting.corporate_actions import is_applicable, is_split
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_repository import (
    CorporateActionRepository,
)


class SorSplitSource:
    def __init__(self, session: Session) -> None:
        self._repo = CorporateActionRepository(session)
        # symbol -> (start, end, splits in [start, end])
        self._cache: dict[str, tuple[date, date, list[CorporateAction]]] = {}

    def splits_for(self, symbol: str, start_date: date, end_date: date) -> list[CorporateAction]:
        key = symbol.upper()
        cached = self._cache.get(key)
        if cached is None or cached[0] > start_date or cached[1] < end_date:
            window_start = min(start_date, cached[0]) if cached else start_date
            window_end = max(end_date, cached[1]) if cached else end_date
            rows = self._repo.get_actions_for_symbols_between(
                symbols=[key], start_date=window_start, end_date=window_end
            )
            splits = [
                contract
                for contract in (CorporateActionRepository.to_contract(row) for row in rows)
                if is_split(contract)
            ]
            self._cache[key] = (window_start, window_end, splits)
            cached = self._cache[key]
        return [a for a in cached[2] if start_date <= a.effective_date <= end_date]

    def invalidate(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._cache.clear()
        else:
            self._cache.pop(symbol.upper(), None)


class SorCorporateActionSource:
    """The applicable actions (splits and cash dividends) stored for ``symbols`` with an
    ex-date in the window — what a research run applies (plan 5d, D7)."""

    def __init__(self, session: Session) -> None:
        self._repo = CorporateActionRepository(session)

    def actions_for(
        self, *, symbols: Sequence[str], start_date: date, end_date: date
    ) -> list[CorporateAction]:
        rows = self._repo.get_actions_for_symbols_between(
            symbols=sorted({s.upper() for s in symbols}),
            start_date=start_date,
            end_date=end_date,
        )
        return [
            contract
            for contract in (CorporateActionRepository.to_contract(row) for row in rows)
            if is_applicable(contract)
        ]
