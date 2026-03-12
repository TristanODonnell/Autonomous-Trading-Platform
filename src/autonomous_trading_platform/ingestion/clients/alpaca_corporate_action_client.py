import os

import httpx


def fetch_corporate_actions(limit: int = 100):
    url = "https://data.alpaca.markets/v1/corporate-actions"

    headers = {
        "accept": "application/json",
        "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY"),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY"),
    }

    params = {"limit": limit, "sort": "asc"}

    r = httpx.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json()
