from __future__ import annotations

import httpx

from autonomous_trading_platform.config.settings import Settings

CORPORATE_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"

# Alpaca's maximum page size for /v1/corporate-actions.
_MAX_PAGE_LIMIT = 1000


def fetch_corporate_actions(
    *,
    limit: int = _MAX_PAGE_LIMIT,
    start: str | None = None,
    end: str | None = None,
    symbols: list[str] | None = None,
    types: list[str] | None = None,
) -> dict:
    """Fetch corporate actions from Alpaca, handling pagination automatically.

    Every list Alpaca returns is kept (``cash_dividends``, ``forward_splits``,
    ``reverse_splits``, ``stock_dividends``, ``spin_offs``, ``cash_mergers``,
    ``stock_mergers``, ``name_changes``, ``unit_splits``, ``worthless_removals``, …)
    and merged across pages under the same key. Items carry no type field of their
    own: the list key is the type.

    Parameters
    ----------
    start, end:
        ISO date strings. Alpaca matches actions whose dates fall in the window;
        with neither given it returns only today's actions, so callers should
        always pass a window.
    symbols:
        Symbols to filter by. If None, fetches all symbols (large; paginated).
    types:
        Alpaca ``types`` filter (e.g. ``["forward_split", "cash_dividend"]``).
        None fetches every type.
    """
    settings = Settings()
    headers = {
        "accept": "application/json",
        "APCA-API-KEY-ID": settings.broker_api_key,
        "APCA-API-SECRET-KEY": settings.broker_api_secret,
    }

    base_params: dict = {"limit": min(limit, _MAX_PAGE_LIMIT), "sort": "asc"}
    if start:
        base_params["start"] = start
    if end:
        base_params["end"] = end
    if symbols:
        base_params["symbols"] = ",".join(s.upper() for s in symbols)
    if types:
        base_params["types"] = ",".join(types)

    merged: dict[str, list] = {}
    page_token: str | None = None

    while True:
        params = {**base_params}
        if page_token:
            params["page_token"] = page_token

        r = httpx.get(CORPORATE_ACTIONS_URL, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()

        actions_block = payload.get("corporate_actions", {}) or {}
        for list_key, items in actions_block.items():
            if isinstance(items, list):
                merged.setdefault(str(list_key), []).extend(items)

        page_token = payload.get("next_page_token")
        if not page_token:
            break

    return {"corporate_actions": merged, "next_page_token": None}
