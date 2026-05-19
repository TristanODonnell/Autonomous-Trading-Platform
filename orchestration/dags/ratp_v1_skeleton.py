from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.empty import EmptyOperator

with DAG(
    dag_id="ratp_v1_skeleton",
    description="RATP v1 skeleton: ingest → snapshot → evaluate → intents → reconcile",
    start_date=datetime(2026, 1, 1),
    schedule="*/5 * * * *",
    catchup=False,
    tags=["ratp", "v1"],
) as dag:
    ingest = EmptyOperator(task_id="ingest_market_bars")
    snapshot = EmptyOperator(task_id="build_universe_version")
    evaluate = EmptyOperator(task_id="evaluate_strategy")
    intents = EmptyOperator(task_id="emit_order_intents")
    reconcile = EmptyOperator(task_id="reconcile")

    ingest >> snapshot >> evaluate >> intents >> reconcile
