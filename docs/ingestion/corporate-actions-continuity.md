# Corporate Action Continuity Checks (v1)

## Raw vs Adjusted Policy

We store BOTH:

1) Raw bars
- provider-native prices/volume as received

2) Adjusted bars
- derived using corporate action adjustment_factor

Adjustment_factor:
- multiplicative factor applied to prices (and inverse for share quantity)
- must ensure continuity across split boundaries

## Continuity Invariants

For any split effective at date D:

- adjusted_close(D-1) and adjusted_open(D) must be continuous within tolerance

Continuity tolerance (v1):
- |adj_close_prev - adj_open_next| / adj_close_prev <= 0.5%
  else record CORPORATE_ACTION_CONTINUITY_BREACH

## Corporate Action Application Rules (v1)

- Splits adjust historical prices and volumes via adjustment factors
- Dividends tracked as events; adjusted series may optionally incorporate dividend adjustments (documented choice)

v1 locked choice:
- Apply split adjustments into adjusted bars
- Record dividends as CorporateAction events (no dividend-adjusted price series in v1)

## Incident Handling

If continuity breach occurs:
- exclude affected symbol from evaluation for cycle
- record incident
- require manual review before re-enabling symbol
