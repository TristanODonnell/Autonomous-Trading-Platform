from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
    check_ingestion_readiness_job,
)
from autonomous_trading_platform.scheduler.jobs.run_order_reconciliation_job import (
    run_order_reconciliation_job,
)
from autonomous_trading_platform.scheduler.jobs.run_order_submission_job import (
    run_order_submission_job,
)
from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
    run_trading_evaluation_job,
)

with DAG(
    dag_id="market_trading_dag",
    schedule="*/5 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
) as dag:
    readiness = PythonOperator(
        task_id="check_ingestion_readiness",
        python_callable=check_ingestion_readiness_job,
    )

    evaluate = PythonOperator(
        task_id="run_trading_evaluation",
        python_callable=run_trading_evaluation_job,
    )

    submit = PythonOperator(
        task_id="run_order_submission",
        python_callable=run_order_submission_job,
    )

    reconcile = PythonOperator(
        task_id="run_order_reconciliation",
        python_callable=run_order_reconciliation_job,
    )

    readiness >> evaluate >> submit >> reconcile
