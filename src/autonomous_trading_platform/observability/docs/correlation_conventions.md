# Correlation conventions

`runtime_job_runs.correlation_id` is the stable cross-signal correlation key.

- Runtime APIs return `correlation_id`, `tempo_link`, and `loki_link` for job-run rows.
- Tempo spans carry `ratp.correlation_id`; spans created inside `RuntimeJobRunner` also carry `ratp.job_run_id`, `ratp.job_name`, and ambient `ratp.environment` when present.
- Loki queries use structured log field `correlation_id`.
- Prometheus metrics must not use `correlation_id`, `run_id`, or `job_run_id` as labels.

Dashboard drilldowns should start from runtime API rows, then pivot to Tempo and Loki through the returned links. Prometheus panels should aggregate by the approved low-cardinality labels only.
