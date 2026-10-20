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

   The last command must print `"status": "ok"`.

4. **Reset the portfolio drawdown peak** to the account's real equity, or the governance
   check will report a permanent drawdown against the old (wrong) peak:

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
