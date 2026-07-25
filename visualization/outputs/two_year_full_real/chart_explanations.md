# Chart Explanations

> **Data mode:** Real fill / live data
> **Period:** 2023-01-02 → 2024-12-31
> **Symbols:** SPY, QQQ, IWM, DIA, VTI, TLT, GLD, XLK, XLF, XLE, XLV, XLU, XLY, XLP, XLI, AAPL, MSFT, NVDA, GOOGL, META, AMZN, TSLA, AVGO, ORCL, AMD, ADBE, CRM, INTU, QCOM, TXN, CSCO, IBM, INTC, ACN, NOW, JPM, BAC, WFC, GS, MS, V, MA, AXP, BLK, C, COF, SPGI, ICE, UNH, LLY, JNJ, ABBV, MRK, ABT, TMO, AMGN, GILD, PFE, ISRG, CVS, REGN, VRTX, HD, MCD, COST, SBUX, NKE, LOW, TGT, BKNG, WMT, PG, KO, PEP, PM, MO, CL, XOM, CVX, COP, SLB, EOG, OXY, CAT, HON, UPS, GE, RTX, DE, EMR, FDX, WM, NEE, DUK, SO, LIN, SHW, ZTS, ELV, SYK

Each chart below answers a specific question about the platform run.
Read the caveats carefully — several charts use synthetic equity curves.

---

## 01 — Equity Curve

**Question answered:** Did the platform grow capital and beat its benchmark?
**How to read it:** Green line = platform equity. Blue = synthetic SPY benchmark. Dotted gray = cash / risk-free. Dashed horizontal lines show 15 / 20 / 25% target return hurdles (not financial advisory standards). Colored triangles mark governance and safety events.
**Key takeaway:** Shows whether the platform ended above the synthetic benchmark and whether it cleared any of the target return hurdles.
**Important caveat:** Platform and benchmark lines are synthetic unless real fill data is present. This is a backtest/replay demonstration, not live client performance.

---

## 02 — Drawdown Analysis

**Question answered:** How deep did the platform fall and for how long?
**How to read it:** Red filled area = platform drawdown from peak. Blue dashed = benchmark drawdown. Dotted horizontal lines mark WARNING / PROBATION / SUSPENDED governance ladder thresholds (scaled from the artifact's max_drawdown_limit setting). Bottom panel shows days underwater (in drawdown).
**Key takeaway:** Answers: what was the worst loss period, how long did it last, and did the platform recover?
**Important caveat:** Drawdown is computed on synthetic equity unless real fills are present. Ladder thresholds are per-strategy governance limits, not portfolio-level stops.

---

## 03 — Monthly Returns

**Question answered:** Was performance consistent across the year?
**How to read it:** Left heatmap: monthly return % per month (rows = months, columns = years). Green = positive, red = negative. Right panel: distribution of daily returns with mean (yellow) and ±1 std band.
**Key takeaway:** A cluster of red months or a heavily skewed distribution would suggest results were concentrated in a few periods.
**Important caveat:** Monthly returns are computed from synthetic equity unless real fills are present.

---

## 04 — Performance Table

**Question answered:** What are the headline performance numbers?
**How to read it:** 14 key metrics side by side: Platform vs Benchmark vs Difference. Green = outperformance, red = underperformance.
**Key takeaway:** Single reference for quoting Sharpe, CAGR, Max DD, and other key figures.
**Important caveat:** All figures derived from synthetic equity curve unless real fills present. Do not cite as live performance.

---

## 05 — Governance Timeline

**Question answered:** What decisions were made and when did they happen?
**How to read it:** Top panel = equity curve. Middle = event swim lanes by category (Safety, Governance, Settings, Allocation). Circles = applied events, X = skipped. Bottom = strategies in breach over time.
**Key takeaway:** This is the most reliable chart — it shows **real** governance events from the artifact, not synthetic data.
**Important caveat:** Event timing is real. The equity line behind the events is synthetic unless real fills are present.

---

## 06 — Operational Health

**Question answered:** Did the platform run reliably?
**How to read it:** Calendar heatmap = tick success/failure by day. Health bar = ok / degraded / critical status over time. Alert timeline = active and critical alert periods. Donut = overall tick completion breakdown.
**Key takeaway:** This chart uses **real** operational data from the artifact — system_health, alert counts, and tick errors are not synthetic.
**Important caveat:** Health metrics reflect the replay environment, not production. Degraded status in a replay may differ from live operating conditions.

---

## 07 — Estimated Platform Contribution

**Question answered:** Which platform systems contributed to (or cost) performance?
**How to read it:** Horizontal bars = directional contribution score per domain. Scores are heuristic estimates derived from artifact metadata (research pass rate, governance actions, Sharpe, max DD, etc.). They are NOT exact P&L attribution.
**Key takeaway:** Directional signal about which platform layers appear to have added or subtracted value. Treat as a qualitative guide, not a measurement.
**Important caveat:** **Directional estimate only.** Full contribution attribution requires strategy-level P&L, allocation history, and per-fill execution data. These are not present in this artifact.

---

## 08 — Execution Quality

**Question answered:** How well did the execution layer perform?
**How to read it:** 6 panels: slippage distribution, adverse fill rate over time, fill latency distribution, slippage over time, slippage vs volatility scatter.
**Key takeaway:** Shows whether execution cost and quality remained stable across the period.
**Important caveat:** All execution metrics are **synthetic** (modeled ~4.2 bps avg slippage, ~10% adverse rate) unless real fills are present in the artifact. Do not cite these figures as measured trading costs.

---

## 09 — Benchmark Gauntlet

**Question answered:** Did the platform beat basic alternatives?
**How to read it:** Each row is a baseline: Cash, synthetic SPY benchmark, and three target return hurdles (15% / 20% / 25%). Bar length = final portfolio value. Text shows total return % and difference vs platform. Labels indicate data source (synthetic / target hurdle / unavailable).
**Key takeaway:** Quick visual for 'did we beat the obvious alternatives?' — the three hurdle lines are the most relevant for internal goal-setting.
**Important caveat:** External references (actual SPY, QQQ, VTI) are **not available** — no external price data is loaded. Hurdles are simple total return targets, not financial advisor standards.

---

## 10 — Cost Sensitivity

**Question answered:** How sensitive are results to costs and slippage assumptions?
**How to read it:** Table of scenarios with increasing additional slippage (0 to +50 bps). Each row re-estimates total return, CAGR, and final value after applying the extra cost drag. Assumes estimated annual portfolio turnover.
**Key takeaway:** Shows how fragile (or robust) the return estimate is to cost assumptions.
**Important caveat:** **Rough sensitivity estimate.** Turnover is estimated from rebalance frequency and symbol count — actual trade count is not recorded in this artifact. Results should be re-run with real fill data for precision.

---

## 11 — Rolling Risk Metrics

**Question answered:** Were results stable over time, or driven by one lucky period?
**How to read it:** Four panels: rolling 30-day return, rolling volatility, rolling Sharpe ratio, and rolling drawdown. Green = platform, blue = benchmark where applicable.
**Key takeaway:** If Sharpe spikes in one quarter and collapses in others, results may not be reproducible. Stable rolling metrics suggest the strategy performed consistently.
**Important caveat:** All rolling metrics computed on synthetic equity unless real fills present. Rolling windows of 30 days are short — high variance is expected.

---

## 12 — Exposure & Allocation

**Question answered:** What was the platform actually holding or exposed to?
**How to read it:** Shows available exposure data from the artifact. If per-tick holdings data is absent, a summary of available fields and what would be needed is shown instead.
**Key takeaway:** Identifies whether the platform was fully invested, cash-heavy, or concentrated in specific holdings throughout the period.
**Important caveat:** This artifact does not contain per-tick position weights or symbol-level allocation history. The chart shows aggregate exposure fields (gross/net) from the end-of-run risk snapshot only.

---

*Generated by the Autonomous Trading Platform visualization package.*
