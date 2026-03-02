# Stress Test Specification 

## Required Stress Scenarios (v1)

---

### 1. Gap Shock

Inject open gap of ±5–20%.

Validate:

- Stop-loss performance
- Slippage impact
- Max drawdown response

---

### 2. Volatility Spike

Multiply intrabar range × 3–5.

Observe:

- Slippage increase
- Execution degradation
- Risk constraint behavior

---

### 3. Data Outage

Remove N consecutive bars.

Engine must:

- Skip evaluation
- Freeze entries
- Record incident

---

### 4. Delayed Bars

Simulate SLA miss.

Engine must:

- Skip cycle
- Record incident
- Continue deterministically

---

### 5. Extreme Slippage

Multiply slippage model × 5.

Observe:

- Strategy robustness
- Exposure spike risk

---

### 6. Partial Liquidity Collapse

Reduce effective bar volume.

Verify:

- Partial fills handled correctly
- No phantom fills
- Correct state transitions

---

## Acceptance Criteria

For any stress run:

- All state transitions recorded
- No ambiguous order states
- Position accounting remains consistent
- Incident recorded in debug report