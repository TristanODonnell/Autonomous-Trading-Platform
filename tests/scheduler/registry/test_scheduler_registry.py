from autonomous_trading_platform.scheduler.registry.scheduler_registry import (
    SCHEDULER_REGISTRY,
    SchedulerJobDefinition,
)

EXPECTED_JOB_NAMES = {
    "market_ingestion_cycle",
    "feature_pipeline_cycle",
    "trading_cycle",
    "strategy_allocation_rebalance_cycle",
    "strategy_auto_promotion_cycle",
    "strategy_auto_demotion_cycle",
    "corporate_action_ingestion_cycle",
    "factor_exposure_monitoring_cycle",
    "factor_neutralization_verification_cycle",
    "experiment_pipeline_cycle",
}


def test_scheduler_registry_contains_expected_jobs():
    assert set(SCHEDULER_REGISTRY.keys()) == EXPECTED_JOB_NAMES


def test_scheduler_registry_values_are_job_definitions():
    for job_name, definition in SCHEDULER_REGISTRY.items():
        assert isinstance(definition, SchedulerJobDefinition)
        assert definition.job_name == job_name


def test_scheduler_registry_definitions_have_schedule():
    for definition in SCHEDULER_REGISTRY.values():
        assert definition.cron is not None or definition.interval_seconds is not None
        assert not (definition.cron is not None and definition.interval_seconds is not None)


def test_scheduler_registry_definitions_have_manual_trigger_flag():
    for definition in SCHEDULER_REGISTRY.values():
        assert isinstance(definition.manual_trigger_enabled, bool)


def test_scheduler_registry_definitions_have_stable_lock_keys():
    lock_keys = [definition.lock_key for definition in SCHEDULER_REGISTRY.values()]

    assert all(lock_keys)
    assert len(lock_keys) == len(set(lock_keys))

    for definition in SCHEDULER_REGISTRY.values():
        assert definition.lock_key == f"scheduler:{definition.job_name}"


def test_scheduler_registry_has_expected_schedules():
    assert SCHEDULER_REGISTRY["market_ingestion_cycle"].interval_seconds == 300
    assert SCHEDULER_REGISTRY["feature_pipeline_cycle"].interval_seconds == 300
    assert SCHEDULER_REGISTRY["trading_cycle"].interval_seconds == 300

    assert SCHEDULER_REGISTRY["strategy_allocation_rebalance_cycle"].cron == "0 21 * * 1-5"
    assert SCHEDULER_REGISTRY["strategy_auto_promotion_cycle"].cron == "30 21 * * 1-5"
    assert SCHEDULER_REGISTRY["strategy_auto_demotion_cycle"].cron == "45 21 * * 1-5"
    assert SCHEDULER_REGISTRY["corporate_action_ingestion_cycle"].cron == "0 22 * * 1-5"
    assert SCHEDULER_REGISTRY["factor_exposure_monitoring_cycle"].cron == "15 22 * * 1-5"
    assert SCHEDULER_REGISTRY["factor_neutralization_verification_cycle"].cron == "30 22 * * 1-5"
    assert SCHEDULER_REGISTRY["experiment_pipeline_cycle"].cron == "0 23 * * 1-5"


def test_scheduler_registry_has_short_interval_smoke_test_candidate():
    short_interval_jobs = [
        definition
        for definition in SCHEDULER_REGISTRY.values()
        if definition.interval_seconds is not None and definition.interval_seconds <= 300
    ]

    assert short_interval_jobs != []
