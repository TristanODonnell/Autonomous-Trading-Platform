from __future__ import annotations

from autonomous_trading_platform.contracts.runtime.ticker_lifecycle_event import (
    TickerLifecycleEvent,
    TickerLifecycleEventType,
)

from .core import Rule

TICKER_LIFECYCLE_EVENT_RULES: list[Rule[TickerLifecycleEvent]] = [
    Rule(
        code="SYMBOL_NON_EMPTY",
        field="symbol",
        check=lambda event, _ctx: isinstance(event.symbol, str) and event.symbol.strip() != "",
        message=lambda event, _ctx: "symbol must be a non-empty string",
    ),
    Rule(
        code="SOURCE_NON_EMPTY",
        field="source",
        check=lambda event, _ctx: isinstance(event.source, str) and event.source.strip() != "",
        message=lambda event, _ctx: "source must be a non-empty string",
    ),
    Rule(
        code="SUCCESSOR_REQUIRED_FOR_MAPPING_EVENTS",
        field="successor_symbol",
        check=lambda event, _ctx: (
            event.event_type
            not in {
                TickerLifecycleEventType.RENAME,
                TickerLifecycleEventType.MERGER,
                TickerLifecycleEventType.SUCCESSOR,
            }
            or (isinstance(event.successor_symbol, str) and event.successor_symbol.strip() != "")
        ),
        message=lambda event, _ctx: (
            "successor_symbol is required for rename, merger, and successor events"
        ),
    ),
    Rule(
        code="SUCCESSOR_MUST_NOT_EQUAL_SYMBOL",
        field="successor_symbol",
        check=lambda event, _ctx: (
            event.successor_symbol is None or event.successor_symbol != event.symbol
        ),
        message=lambda event, _ctx: "successor_symbol cannot equal symbol",
    ),
]
