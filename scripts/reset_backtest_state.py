"""Reset all backtest state tables for a clean run."""

import os

from sqlalchemy import create_engine, text

url = os.environ.get("DATABASE_URL", "postgresql+psycopg://ratp:ratp_password@localhost:5433/ratp")
engine = create_engine(url)

# Get actual table names from DB
with engine.connect() as conn:
    result = conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    )
    existing = {row[0] for row in result}

print("Tables in DB:")
for t in sorted(existing):
    print(f"  {t}")

# Tables to clear — each in its own transaction so one failure doesn't block others
candidates = [
    # Child tables first (FK constraints).
    # ingestion_runs must come before missing_bar_incidents because
    # missing_bar_incidents.ingestion_run_id → ingestion_runs (FK).
    # dataset_versions must come after simulation_runs for the same reason.
    "missing_bar_incidents",
    "runtime_job_run_steps",
    "runtime_soak_reports",
    "shadow_divergences",
    "shadow_comparison_snapshots",
    "shadow_runs",
    "position_snapshot_items",
    "portfolio_signal_batch_items",
    "portfolio_signal_intents",
    "portfolio_netted_signals",
    "drawdown_governance_ladder_transitions",
    "strategy_health_transitions",
    "strategy_quality_score_history",
    "strategy_live_performance_snapshots",
    "strategy_runtime_states",
    "strategy_control_states",
    "strategy_factor_exposures",
    "factor_exposure_snapshots",
    "factor_neutralization_runs",
    "portfolio_factor_exposures",
    "allocation_rebalance_history",
    "governance_audit_events",
    "audit_logs",
    "fill_quality_metrics",
    # Core state tables
    "fills",
    "signals",
    "order_intents",
    "tracked_orders",
    "broker_orders",
    "position_snapshots",
    "cash_snapshots",
    "broker_account_snapshots",
    "reconciliation_snapshots",
    "risk_snapshots",
    "run_manifests",
    "strategy_governance",
    "allocation_overrides",
    "runtime_control_state",
    "kill_switch_state",
    "strategy_health_states",
    "portfolio_drawdown_governance_state",
    "drawdown_governance_ladder_states",
    "blended_metrics_snapshots",
    "portfolio_construction_runs",
    "correlation_snapshots",
    "covariance_snapshots",
    "risk_budget_snapshots",
    "optimizer_runs",
    "metrics_summary",
    "black_litterman_research_runs",
    "simulation_runs",
    "experiments",
    "universe_members",
    "universe_rebalance_runs",
    "universe_rotation_records",
    "universe_snapshots",
    "universe_versions",
    "ingestion_checkpoints",
    "ingestion_runs",
    "symbol_date_coverages",  # references dataset_versions — must come before it
    "dataset_versions",
    "runtime_job_runs",
    "operational_alerts",
]

print("\nClearing tables:")
for table in candidates:
    if table not in existing:
        continue
    with engine.begin() as conn:
        result = conn.execute(text(f"DELETE FROM {table}"))
        print(f"  {table}: {result.rowcount} rows deleted")

print("\nDone.")
