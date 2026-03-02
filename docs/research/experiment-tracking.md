# Experiment Tracking 

## Required Output Artifacts

Every backtest run must produce:

1. run_manifest.json
2. metrics_summary.json
3. trades_journal.parquet
4. debug_report.json

---

## run_manifest.json

Must include:

- git_commit
- dataset_version
- universe_version
- strategy_config
- cost_model
- fill_model
- capital_bucket
- random_seed
- start_date
- end_date

---

## metrics_summary.json

Required metrics:

- Total Return
- CAGR
- Sharpe Ratio
- Sortino Ratio
- Max Drawdown
- Win Rate
- Profit Factor
- Average Trade Duration
- Turnover

---

## trades_journal.parquet

Fields:

- timestamp
- symbol
- side
- quantity
- fill_price
- slippage
- commission
- intent_id
- order_id

---

## debug_report.json

Must log:

- Data gaps
- Outlier exclusions
- SLA breaches
- Partial fills
- Stress injections triggered

---

## Reproducibility Guarantee

Given identical:

- Code commit
- Dataset version
- Strategy config
- Cost/fill models

The engine must reproduce identical results.