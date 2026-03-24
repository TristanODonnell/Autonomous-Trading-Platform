from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
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
        incident_type="corporate_action_ingestion_failure",
        details={
            "dag_id": context["dag"].dag_id,
            "task_id": context["task_instance"].task_id,
            "run_id": context["run_id"],
            "execution_date": str(context.get("execution_date")),
        },
    )


with DAG(
    dag_id="corporate_action_ingestion_dag",
    description="Run the daily corporate actions ingestion cycle.",
    default_args=default_args,
    start_date=datetime(2026, 3, 12),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    tags=["corporate-actions", "ingestion", "scheduler"],
) as dag:
    PythonOperator(
        task_id="run_corporate_action_ingestion_cycle",
        python_callable=run_corporate_action_ingestion_cycle,
        execution_timeout=timedelta(minutes=10),
        on_failure_callback=on_failure_callback,
    )
