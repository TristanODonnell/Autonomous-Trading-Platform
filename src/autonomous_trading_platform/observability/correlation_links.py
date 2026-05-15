from __future__ import annotations

from urllib.parse import quote


def tempo_link(correlation_id: str | None) -> str | None:
    if not correlation_id:
        return None
    query = quote(f'{{ratp.correlation_id="{correlation_id}"}}', safe="")
    return f'/explore?left={{"datasource":"Tempo","queries":[{{"query":"{query}"}}]}}'


def loki_link(correlation_id: str | None) -> str | None:
    if not correlation_id:
        return None
    query = quote(f'{{correlation_id="{correlation_id}"}}', safe="")
    return f'/explore?left={{"datasource":"Loki","queries":[{{"expr":"{query}"}}]}}'
