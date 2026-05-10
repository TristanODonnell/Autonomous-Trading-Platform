import httpx

from autonomous_trading_platform.config.settings import Settings


def fetch_corporate_actions(limit: int = 100):
    settings = Settings()
    url = "https://data.alpaca.markets/v1/corporate-actions"

    headers = {
        "accept": "application/json",
        "APCA-API-KEY-ID": settings.broker_api_key,
        "APCA-API-SECRET-KEY": settings.broker_api_secret,
    }

    params = {"limit": limit, "sort": "asc"}

    r = httpx.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json()
