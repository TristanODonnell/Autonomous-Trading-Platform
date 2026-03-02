# Cost Model 

## v1 Linear Cost Model

Total cost per fill:

commission
+ (spread_pct × price)
+ (slippage_pct × price)

Parameters:

- commission_per_trade
- slippage_pct
- spread_pct

All parameters stored in RunManifest.

---

## Slippage Model (v1 VolumeShare-Like)

participation_rate = order_qty / bar_volume

slippage_multiplier = base_slippage × participation_rate

effective_slippage = min(slippage_multiplier, max_slippage_cap)

Effective fill price:

price ± effective_slippage

---

## Determinism Requirement

Cost model must:

- Produce identical output for identical input
- Contain no randomness
- Be fully parameterized
- Be recorded in RunManifest