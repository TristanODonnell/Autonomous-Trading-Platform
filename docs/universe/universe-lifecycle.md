# Universe Lifecycle Handling

## Objective
Ensure symbol changes, mergers, and delistings
do not break historical reproducibility.

---

## Lifecycle Events Covered

- Delisting
- Merger (stock-for-stock)
- Merger (cash)
- Symbol change
- Reverse split
- Bankruptcy

---

## Ticker Mapping Table

We maintain a SymbolMapping table:

Fields:
- old_symbol
- new_symbol
- effective_date
- event_type
- corporate_action_id

Rule:
Historical bars remain stored under original symbol.
Mapping is applied only for forward continuity logic.

---

## Delisting Handling

If a symbol delists:

- It remains in historical UniverseSnapshots
- No retroactive removal allowed
- Historical backtests must still evaluate it

If delisting occurs mid-holding:
- Exit logic must trigger at last tradable bar

---

## Merger Handling

Stock-for-stock:
- Position quantity adjusted using ratio

Cash merger:
- Position closed at effective date price

---

## Symbol Change

Example:
FB → META

Rules:
- Historical data remains under FB
- Mapping table links FB to META
- Universe membership uses symbol valid on snapshot date

---

## Hard Rule

Historical runs MUST use:
UniverseSnapshot membership as-of that date.

No survivorship leakage allowed.