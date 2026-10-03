# Survivorship-Safe Replay

## The problem

The replay's universe *selection* was already point-in-time: rotations fire on
replay dates and rank by dollar volume from bars before each date. The *candidate
list* was not:

1. `AlpacaScreenerProvider` starts from `get_all_assets(status=ACTIVE)` — assets
   active **today**. Tickers delisted since the replay date are missing, and
   Alpaca's inactive list is not a substitute (ATVI, PXD, HES are absent from it;
   SIVB, FRC return "asset not found"), even though Alpaca still serves their
   historical bars.
2. The replay only ingests the fixture's hand-picked `symbols`, so the universe
   can only ever choose from a list written with hindsight.
3. Nothing recorded delistings, so a held position in a dead ticker had no price:
   the exit order was skipped and the position sat at a stale mark forever.
4. Research used the universe active at the tick date (dropping names that died
   inside its lookback window) and never ran `SurvivorshipGuard`.
5. `UniverseHistoryService` looked historical dates up with
   `get_active_version`, which only matches the *currently* ACTIVE version —
   rotation retires the previous one, so past dates resolved to nothing.

Measured on 2023-01-03 (S&P 500, same ranking rule): 21 of the 398 qualifying
names are not active/tradable today (PXD and ATVI inside the top 100; also SIVB,
FRC, SBNY, HES, DFS, CTLT, WBA). None of them were visible to the old screener.

## What changed

| Fix | Where |
|---|---|
| Point-in-time S&P 500 membership bundled with the package (fja05680/sp500, MIT) | `universe/reference_data/`, `universe/services/index_constituents_service.py` |
| `PointInTimeIndexProvider` — index members as of the date, ranked with the same dollar-volume rule (`rank_symbols_by_dollar_volume`) | `universe/providers/point_in_time_index_provider.py` |
| Fixture `symbol_pool` — replay symbols = top N of that pool as of the start date, resolved once at run start; the same source drives the bootstrap and every rotation's screener | `platform/replay/platform_replay_config.py`, `cli/commands/platform.py`, `platform_backtest_service.py`, `platform_replay/universe_hooks.py` |
| Point-in-time version lookup (`get_version_effective_at`: ACTIVE or RETIRED covering the date) used by `UniverseHistoryService` | `universe_version_repository.py`, `universe_history_service.py` |
| Research universe anchored at the research-window start; `SurvivorshipGuard` enforced (tick fails with `survivorship_guard: …`); validation's survivorship stage gets the scope | `platform_replay/research_hooks.py` |
| Delisting detection: no bars for 5 market days (days other symbols traded) → `DELISTING` lifecycle event effective the day after the last bar | `universe/services/delisting_detection_service.py`, `platform_replay/ingestion_hooks.py` |
| Delisted members dropped from the trading universe | `scheduler/common/trading_cycle_common.py` |
| Simulated broker prices a delisted symbol at its last close, so the normal exit-delta path sells the holding | `execution/clients/simulated_broker_client.py` |

## Using it

```yaml
platform_replay:
  name: my_replay
  start: 2023-01-03
  end: 2024-12-31
  symbol_pool:
    source: sp500_point_in_time
    top_n: 100          # ingested for the whole replay
  symbols: [SPY, QQQ]   # optional extras (benchmarks), always ingested
```

`atp platform backtest plan` shows the pool without resolving it; `run` resolves
it against Alpaca market data at start. `--symbols` on the CLI overrides the pool.
Without `symbol_pool`, fixtures behave as before (hand-picked list, today's active
assets for the screener).

## Residual gaps

- **The pool is fixed at the start date.** Companies that join the index later
  are not ingested, so rotations can't pick them. That is an omission, not
  look-ahead, but long replays drift from the real index.
- **Large caps only.** The membership file covers the S&P 500; smaller or
  non-index names still need a paid source (Norgate, Sharadar, CRSP).
- **Exit at the last exchange close is optimistic for bankruptcies** (SIVB was
  halted, then traded OTC far lower). For acquisitions it is close to the deal price.
- **Renames look like delistings** (ABC→COR, RE→EG, FLT→CPAY). The holding exits
  at a fair price instead of carrying over to the new ticker.
- **Detection lags by 5 market days**, during which the position is marked at its
  last price.
- Share-class tickers with dots (BRK.B, BF.B) are excluded by both screeners.
