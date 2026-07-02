from __future__ import annotations

import httpx

from autonomous_trading_platform.config.settings import Settings


def fetch_corporate_actions(
    *,
    limit: int = 100,
    start: str | None = None,
    end: str | None = None,
    symbols: list[str] | None = None,
) -> dict:
    """Fetch corporate actions from Alpaca, handling pagination automatically.

    Parameters
    ----------
    start:
        ISO date string (e.g. "2023-10-01"). If None, Alpaca defaults to recent events.
    end:
        ISO date string (e.g. "2024-12-31"). If None, Alpaca defaults to today.
    symbols:
        Symbols to filter by. If None, fetches all symbols.
    """
    settings = Settings()
    url = "https://data.alpaca.markets/v1/corporate-actions"
    headers = {
        "accept": "application/json",
        "APCA-API-KEY-ID": settings.broker_api_key,
        "APCA-API-SECRET-KEY": settings.broker_api_secret,
    }

    base_params: dict = {"limit": limit, "sort": "asc"}
    if start:
        base_params["start"] = start
    if end:
        base_params["end"] = end
    if symbols:
        base_params["symbols"] = ",".join(s.upper() for s in symbols)

    all_cash_dividends: list = []
    all_reverse_splits: list = []
    page_token: str | None = None

    while True:
        params = {**base_params}
        if page_token:
            params["page_token"] = page_token

        r = httpx.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()

        actions_block = payload.get("corporate_actions", {})
        all_cash_dividends.extend(actions_block.get("cash_dividends", []))
        all_reverse_splits.extend(actions_block.get("reverse_splits", []))

        page_token = payload.get("next_page_token")
        if not page_token:
            break

    return {
        "corporate_actions": {
            "cash_dividends": all_cash_dividends,
            "reverse_splits": all_reverse_splits,
        },
        "next_page_token": None,
    }
