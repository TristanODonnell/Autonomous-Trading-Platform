from __future__ import annotations

from datetime import timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.scheduler.callbacks.airflow_callbacks import (
    airflow_sla_miss_callback,
    airflow_task_failure_callback,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import (
    run_trading_cycle,
)

settings = Settings()

trading_cycle_timeout = timedelta(seconds=settings.trading_cycle_timeout_seconds)
trading_cycle_sla = timedelta(seconds=settings.trading_cycle_sla_seconds)

with DAG(
    dag_id="market_trading_dag",
    schedule="*/5 * * * 1-5",
    catchup=False,
    sla_miss_callback=airflow_sla_miss_callback,
) as dag:
    run_cycle_task = PythonOperator(
        task_id="run_trading_cycle",
        python_callable=run_trading_cycle,
        execution_timeout=trading_cycle_timeout,
        sla=trading_cycle_sla,
        on_failure_callback=airflow_task_failure_callback,
        retries=settings.trading_cycle_retry_attempts,
        retry_delay=timedelta(seconds=settings.trading_cycle_retry_delay_seconds),
        retry_exponential_backoff=True,
    )
