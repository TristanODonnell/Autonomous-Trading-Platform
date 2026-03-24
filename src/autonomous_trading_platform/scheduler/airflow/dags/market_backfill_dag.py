from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle import (
    run_market_backfill_cycle,
)
from autonomous_trading_platform.scheduler.jobs.handle_ingestion_incident import (
    handle_ingestion_incident,
)

default_args = {
    "owner": "autonomous-trading-platform",
    "depends_on_past": False,
    "retries": 0,
}


def on_failure_callback(context: dict) -> None:
    handle_ingestion_incident(
        incident_type="market_backfill_failure",
        details={
            "dag_id": context["dag"].dag_id,
            "task_id": context["task_instance"].task_id,
            "run_id": context["run_id"],
            "execution_date": str(context.get("execution_date")),
        },
    )


with DAG(
    dag_id="market_backfill_dag",
    description="Run the historical market-data backfill/bootstrap job.",
    default_args=default_args,
    start_date=datetime(2026, 3, 12),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    tags=["market-data", "backfill", "bootstrap", "scheduler"],
) as dag:
    PythonOperator(
        task_id="run_market_backfill_cycle",
        python_callable=run_market_backfill_cycle,
        execution_timeout=timedelta(minutes=30),
        on_failure_callback=on_failure_callback,
    )
