# Resync the ledger from the broker (Box A)

Status: ACTIVE (first used Tue 2026-10-20, after soak day 1)

## When

The platform's books (`cash_snapshots` / `position_snapshots`, source `LEDGER`) chain
forward fill by fill from whatever snapshot came before. They drift from the broker when:

- the box was seeded from a dev dump (the first paper day ran on $3.6M of ledger cash
  against a $100k Alpaca account);
- positions were opened or closed outside the platform (a manual "Close All" on the
  Alpaca dashboard, a broker-side liquidation);
- fills were booked twice or missed during an incident.

Symptoms: `atp execution validate-broker-consistency` reports `drifted`; pre-trade risk
checks reject or allow orders that make no sense for the real account; the portfolio
drawdown governance peak is a number the account never had.

## Procedure

Run on Box A (`ubuntu@ip-172-31-42-34`), from `~/Autonomous-Trading-Platform`, with no
tick in flight (outside market hours, or with the kill switch on).

Orders still open in `tracked_orders` are fine (Finding 25). A fill the broker has
executed that no tick has applied yet is already inside the broker's cash and positions;
the sync commands below hold it back (cash and shares), so the ledger lands on the broker
book when the next tick applies it, and the validator compares against the same
expectation. Each command prints the `pending_fills` it held back. Before this, a resync
taken with such a fill open double-counted it at the next tick (Tue 2026-11-03: AAPL 3
bought at 15:56 ET, resynced at 20:19, applied again Wed 09:30 -> ledger 6 vs broker 3).

1. **Stop new orders** while the books are being rewritten:

   ```bash
   docker compose exec app atp safety enable-kill-switch --reason "ledger resync" --updated-by "$USER"
   ```

2. **Make the broker the truth.** If positions must go flat, close them on the Alpaca
   dashboard ("Close All Positions"; outside market hours the orders queue for the open)
   and wait until the dashboard shows no open positions and no open orders.

3. **Write broker-sourced snapshots.** Both commands take the same fresh run id; cash and
   equity come from `/v2/account`, positions from `/v2/positions`. The snapshots are
   stamped `now`, so they are the newest rows and every later ledger snapshot chains from
   them.

   ```bash
   RUN_ID=$(uuidgen)
   docker compose exec app atp execution sync-broker-state --run-id "$RUN_ID" --paper-only
   docker compose exec app atp execution sync-position-snapshot --run-id "$RUN_ID" --paper-only
   docker compose exec app atp execution validate-broker-consistency --paper-only
   ```

   The last command must print `"status": "ok"`. Outside market hours it can report
   `drifted` on `equity` / `buying_power` alone while `cash` matches: that is after-hours
   marks moving between the sync and the check, not ledger drift. `cash.cash` is checked
   against the broker's cash plus whatever the listed `pending_fills` hold back.

   Then rebuild the strategy sleeves to the same broker book (the snapshots above do not
   touch them). Symbols with an order still open are left for the next session's first
   tick, so run this when nothing is pending, or accept the `skipped_symbols` it lists:

   ```bash
   docker compose exec app atp execution sync-sleeves --paper-only --dry-run
   docker compose exec app atp execution sync-sleeves --paper-only
   ```

   Exit code 1 with `unresolved` means a gap the resync cannot close by itself: a short
   position at the broker (close it on the dashboard), or a symbol with no cost basis.

4. **Reset the portfolio drawdown peak** to the account's real equity, or the governance
   check will report a permanent drawdown against the old (wrong) peak. (Since Finding 25
   the ladder measures live broker equity each tick and only reads the ledger when the
   broker does not answer, so the peak is the one number here that still matters:)

   ```bash
   docker compose exec -T postgres psql -U ratp -d ratp -c \
     "update portfolio_drawdown_governance_state set peak_equity_usd = (select equity from cash_snapshots order by timestamp desc limit 1);"
   ```

5. **Resume:**

   ```bash
   docker compose exec app atp safety disable-kill-switch --reason "ledger resync done" --updated-by "$USER" --confirm-release
   ```

## Verify after the next tick

- `cash_snapshots`: the newest `LEDGER` row's `cash`/`equity` are within a fill of the
  broker's account page.
- `position_snapshot_items` for the newest snapshot match `/v2/positions` symbol for
  symbol.
- No `ORDER_REJECTED_BY_BROKER` audit rows with `insufficient buying power`.

## Notes

- `sync-broker-state` without `--run-id` only updates `broker_account_snapshots`; the
  cash snapshot that the ledger and the risk checks read needs the run id.
- The dollar risk limits (`MAX_GROSS_EXPOSURE`, `MAX_SYMBOL_EXPOSURE`,
  `MAX_DAILY_NOTIONAL_TRADED`) are checked against these snapshots plus the day's fills,
  so a wrong ledger means wrong rejections. Resync first, then judge the limits.
