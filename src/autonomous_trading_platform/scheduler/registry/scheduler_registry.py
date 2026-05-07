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
    "corporate_action_ingestion_cycle": SchedulerJobDefinition(
        job_name="corporate_action_ingestion_cycle",
        cron="0 22 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:corporate_action_ingestion_cycle",
    ),
    "experiment_pipeline_cycle": SchedulerJobDefinition(
        job_name="experiment_pipeline_cycle",
        cron="0 23 * * 1-5",
        interval_seconds=None,
        manual_trigger_enabled=True,
        lock_key="scheduler:experiment_pipeline_cycle",
    ),
}
