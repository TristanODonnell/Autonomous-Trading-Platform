from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchedulerJobDefinition:
    job_name: str
    cron: str | None
    interval_seconds: int | None
    manual_trigger_enabled: bool
    lock_key: str


SCHEDULER_REGISTRY = {
    "market_ingestion_cycle": SchedulerJobDefinition(
        job_name="market_ingestion_cycle",
        cron=None,
        interval_seconds=300,
        manual_trigger_enabled=True,
        lock_key="scheduler:market_ingestion_cycle",
    ),
    "feature_pipeline_cycle": SchedulerJobDefinition(
        job_name="feature_pipeline_cycle",
        cron=None,
        interval_seconds=300,
        manual_trigger_enabled=True,
        lock_key="scheduler:feature_pipeline_cycle",
    ),
    "trading_cycle": SchedulerJobDefinition(
        job_name="trading_cycle",
        cron=None,
        interval_seconds=300,
        manual_trigger_enabled=True,
        lock_key="scheduler:trading_cycle",
    ),
    "strategy_allocation_rebalance_cycle": SchedulerJobDefinition(
        job_name="strategy_allocation_rebalance_cycle",
        cron="0 21 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:strategy_allocation_rebalance_cycle",
    ),
    "strategy_auto_promotion_cycle": SchedulerJobDefinition(
        job_name="strategy_auto_promotion_cycle",
        cron="30 21 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:strategy_auto_promotion_cycle",
    ),
    "strategy_auto_demotion_cycle": SchedulerJobDefinition(
        job_name="strategy_auto_demotion_cycle",
        cron="45 21 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:strategy_auto_demotion_cycle",
    ),
    "corporate_action_ingestion_cycle": SchedulerJobDefinition(
        job_name="corporate_action_ingestion_cycle",
        cron="0 22 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:corporate_action_ingestion_cycle",
    ),
    "factor_exposure_monitoring_cycle": SchedulerJobDefinition(
        job_name="factor_exposure_monitoring_cycle",
        cron="15 22 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:factor_exposure_monitoring_cycle",
    ),
    "factor_neutralization_verification_cycle": SchedulerJobDefinition(
        job_name="factor_neutralization_verification_cycle",
        cron="30 22 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:factor_neutralization_verification_cycle",
    ),
    "experiment_pipeline_cycle": SchedulerJobDefinition(
        job_name="experiment_pipeline_cycle",
        cron="0 23 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:experiment_pipeline_cycle",
    ),
    "correlation_monitoring_cycle": SchedulerJobDefinition(
        job_name="correlation_monitoring_cycle",
        cron="45 21 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:correlation_monitoring_cycle",
    ),
    "risk_budgeting_cycle": SchedulerJobDefinition(
        job_name="risk_budgeting_cycle",
        cron="50 21 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:risk_budgeting_cycle",
    ),
    "drawdown_governance_ladder_cycle": SchedulerJobDefinition(
        job_name="drawdown_governance_ladder_cycle",
        cron="55 21 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:drawdown_governance_ladder_cycle",
    ),
    "strategy_health_lifecycle_cycle": SchedulerJobDefinition(
        job_name="strategy_health_lifecycle_cycle",
        cron="0 22 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:strategy_health_lifecycle_cycle",
    ),
}
