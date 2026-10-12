"""Reset the backtest state of a database so a fixture run starts clean.

Lifted from ``scripts/reset_backtest_state.py`` (which now calls this). Used by the worker's
job runner before every run. Child tables come first so FK constraints hold; a table that
does not exist in this database is skipped.

The worker's pulled datasets are the one thing a reset must keep: ``dataset_versions`` rows
that ``DatasetPullService`` registered (``metadata_json.pulled_from``) survive unless
``keep_pulled_datasets=False``. The Parquet files themselves are never touched.

Refuses to run against a paper or live environment: this wipes shared system-of-record
tables such as ``audit_logs`` and ``governance_audit_events``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

logger = get_logger(__name__)

# Environments where wiping state is acceptable. APP_ENV on Box A is paper (later live).
RESETTABLE_APP_ENVS = frozenset({"backtest", "dev", "test"})

# Order matters: children before parents.
BACKTEST_STATE_TABLES: tuple[str, ...] = (
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
    "strategy_shadow_performance_snapshots",
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
    # Portfolio mode: per-strategy sleeves and active-set membership
    "corporate_action_applications",
    "strategy_sleeve_ledger",
    "strategy_sleeve_snapshots",
    "strategy_sleeve_positions",
    "shadow_sleeve_ledger",
    "shadow_sleeve_snapshots",
    "shadow_sleeve_positions",
    "portfolio_review_decisions",
    "portfolio_scorecards",
    "portfolio_reviews",
    "bench_evaluations",
    "portfolio_membership_transitions",
    "portfolio_memberships",
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
    "feature_dataset_versions",  # feature outputs of the run (no FK; stale rows otherwise outlive resets)
    "dataset_versions",
    "runtime_job_runs",
    "operational_alerts",
)


class ResetRefusedError(RuntimeError):
    """The current APP_ENV is not one whose state may be wiped."""


@dataclass
class ResetResult:
    deleted: dict[str, int] = field(default_factory=dict)
    skipped_missing: list[str] = field(default_factory=list)
    kept_pulled_dataset_versions: int = 0

    @property
    def rows_deleted(self) -> int:
        return sum(self.deleted.values())

    def summary(self) -> dict[str, Any]:
        return {
            "rows_deleted": self.rows_deleted,
            "tables_cleared": sum(1 for n in self.deleted.values() if n),
            "tables_missing": len(self.skipped_missing),
            "kept_pulled_dataset_versions": self.kept_pulled_dataset_versions,
        }


class WorkerStateResetService:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def check_environment(app_env: str | None = None) -> None:
        env = (app_env or os.getenv("APP_ENV") or "").strip().lower()
        if env not in RESETTABLE_APP_ENVS:
            raise ResetRefusedError(
                f"refusing to reset backtest state with APP_ENV={env!r}; "
                f"allowed: {sorted(RESETTABLE_APP_ENVS)}"
            )

    def reset(
        self, *, keep_pulled_datasets: bool = True, app_env: str | None = None
    ) -> ResetResult:
        self.check_environment(app_env)
        bind = self._session.get_bind()
        existing = set(inspect(bind).get_table_names())
        result = ResetResult()
        for table in BACKTEST_STATE_TABLES:
            if table not in existing:
                result.skipped_missing.append(table)
                continue
            if table == "dataset_versions" and keep_pulled_datasets:
                result.deleted[table], result.kept_pulled_dataset_versions = (
                    self._delete_unpulled_dataset_versions()
                )
                continue
            result.deleted[table] = self._session.execute(text(f"DELETE FROM {table}")).rowcount
        self._session.flush()
        logger.info("worker_state_reset.finished", extra=result.summary())
        return result

    def _delete_unpulled_dataset_versions(self) -> tuple[int, int]:
        deleted = kept = 0
        for row in list(self._session.query(DatasetVersions)):
            if isinstance(row.metadata_json, dict) and "pulled_from" in row.metadata_json:
                kept += 1
                continue
            self._session.delete(row)
            deleted += 1
        self._session.flush()
        return deleted, kept
