# v1 Universe Specification

## Objective
Define the exact universe used for v1 vertical slice.

This document locks:
- Universe name
- Eligibility filters
- Rebalance cadence
- Inclusion/exclusion rules

This universe is immutable for v1.

---

## Universe Name

**Universe ID:** v1_iex_top500_liquid  
**Data Source:** Alpaca (IEX feed)  
**Asset Class:** U.S. Equities  
**Exchanges:** NYSE, NASDAQ (IEX-covered)

---

## Eligibility Filters (Locked)

### Price Filter
- Minimum price: $5.00
- Evaluated using previous day close
- Rationale: Avoid penny stocks / microstructure distortion

### Liquidity Filter
- Minimum 30-day average daily dollar volume:
  $10,000,000

ADV calculation:
ADV = average( close_price * daily_volume )

### Market Cap
- Optional for v1: Not enforced
- Rationale: Liquidity filter sufficient

---

## Explicit Exclusions

- ETFs: Excluded
- ADRs: Excluded
- OTC securities: Excluded
- SPACs: Excluded
- Leveraged ETFs: Excluded
- Halted securities: Excluded

---

## Rebalance Cadence

UniverseSnapshot frequency: **Monthly**

Effective:
- First trading day of each month
- Snapshot taken using prior day data

Rationale:
- Reduces turnover
- Minimizes survivorship noise
- Stable vertical slice

---

## Membership Determination Rule

A symbol is included if:
- Passes all filters
- Listed and active as-of snapshot date
- Tradable via Alpaca

---

## Determinism Rule

Given:
- Snapshot date
- Market data history
- Filter configuration

The resulting symbol list must be deterministic.

No dynamic / runtime additions allowed.