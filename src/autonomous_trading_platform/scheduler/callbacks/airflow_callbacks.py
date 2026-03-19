def airflow_task_failure_callback(context: dict) -> None:
    dag = context.get("dag")
    task_instance = context.get("task_instance")

    dag_id = dag.dag_id if dag else "unknown_dag"
    task_id = task_instance.task_id if task_instance else "unknown_task"

    execution_date = context.get("execution_date")
    exception = context.get("exception")

    print("[TASK_FAILURE]")
    print(f"dag_id={dag_id}")
    print(f"task_id={task_id}")
    print(f"execution_date={execution_date}")
    print(f"error={exception}")


def airflow_sla_miss_callback(
    dag,
    task_list,
    blocking_task_list,
    slas,
    blocking_tis,
) -> None:
    print("[SLA_MISS]")
    print(f"dag_id={dag.dag_id}")
    print(f"tasks={task_list}")
    print(f"blocking_tasks={blocking_task_list}")
