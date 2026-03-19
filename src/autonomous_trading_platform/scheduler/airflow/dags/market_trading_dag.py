from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

from autonomous_trading_platform.scheduler.jobs.run_trading_cycle import (
    run_trading_cycle,
)

with DAG(
    dag_id="market_trading_dag",
    schedule="*/5 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
) as dag:
    run_cycle = PythonOperator(
        task_id="run_trading_cycle",
        python_callable=run_trading_cycle,
    )
