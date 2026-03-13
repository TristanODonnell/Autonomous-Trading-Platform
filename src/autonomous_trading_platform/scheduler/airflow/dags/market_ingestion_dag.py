from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from autonomous_trading_platform.scheduler.jobs.handle_ingestion_incident import (
    handle_ingestion_incident,
)
from autonomous_trading_platform.scheduler.jobs.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)

default_args = {
    "owner": "autonomous-trading-platform",
    "depends_on_past": False,
    "retries": 0,
}


def on_failure_callback(context: dict) -> None:
    """
    Record an ingestion incident when the DAG task fails.
    """
    handle_ingestion_incident(
        incident_type="market_ingestion_failure",
        details={
            "dag_id": context["dag"].dag_id,
            "task_id": context["task_instance"].task_id,
            "run_id": context["run_id"],
            "execution_date": str(context.get("execution_date")),
        },
    )


with DAG(
    dag_id="market_ingestion_dag",
    description="Run the 5-minute market data ingestion cycle.",
    default_args=default_args,
    start_date=datetime(2026, 3, 12),
    schedule="*/5 * * * *",
    catchup=False,
    max_active_runs=1,
    tags=["market-data", "ingestion", "scheduler"],
) as dag:
    PythonOperator(
        task_id="run_market_ingestion_cycle",
        python_callable=run_market_ingestion_cycle,
        execution_timeout=timedelta(minutes=4),
        on_failure_callback=on_failure_callback,
    )
