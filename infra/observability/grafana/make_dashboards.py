"""Generate the Infra, Trading Ops and End-of-Day dashboards.

Edit here and re-run (python infra/observability/grafana/make_dashboards.py); the JSON files
are the output. The older dashboards were written by hand and are not covered.
"""

import json
import pathlib

ROOT = pathlib.Path(r"C:\Users\Tristan\PycharmProjects\Autonomous-Trading-Platform")
OUT = ROOT / "infra/observability/grafana/dashboards"

PROM = {"type": "prometheus", "uid": "prometheus"}
PG = {"type": "postgres", "uid": "ratp-postgres"}
# Same Tempo/Loki explore links as the other RATP dashboards (tests require them)
LINKS = [
    {
        "title": "Tempo by correlation_id",
        "type": "link",
        "url": '/explore?left={"datasource":"tempo","queries":[{"query":"{ratp.correlation_id=\\"${correlation_id}\\"}"}]}',
    },
    {
        "title": "Loki by correlation_id",
        "type": "link",
        "url": '/explore?left={"datasource":"loki","queries":[{"expr":"{} | json | correlation_id=\\"${correlation_id}\\""}]}',
    },
]


def dashboard(uid, title, tags, panels, templating, time_from="now-24h", refresh="30s"):
    return {
        "uid": uid,
        "title": title,
        "tags": ["ratp", *tags],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": refresh,
        "time": {"from": time_from, "to": "now"},
        "annotations": {
            "list": [
                {
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                    "builtIn": 1,
                    "hide": True,
                    "enable": True,
                }
            ]
        },
        "links": LINKS,
        "templating": {"list": templating},
        "panels": panels,
    }


def var(name, query, *, include_all=True, label=None):
    return {
        "name": name,
        "label": label or name,
        "type": "query",
        "datasource": PROM,
        "query": query,
        "includeAll": include_all,
        "allValue": ".*" if include_all else None,
        "multi": False,
        "refresh": 1,
        "current": {"text": "All", "value": "$__all"} if include_all else {},
    }


_ids = iter(range(1, 1000))


def panel(
    title,
    ptype,
    x,
    y,
    w,
    h,
    targets,
    *,
    datasource=PROM,
    unit=None,
    description="",
    extra=None,
    thresholds=None,
):
    p = {
        "id": next(_ids),
        "title": title,
        "type": ptype,
        "description": description,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": datasource,
        "targets": targets,
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": thresholds}
    if extra:
        p.update(extra)
    return p


def prom(expr, legend="", ref="A", instant=False):
    t = {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend}
    if instant:
        t["instant"] = True
    return t


def sql(raw, ref="A"):
    return {"refId": ref, "datasource": PG, "rawSql": raw, "format": "table"}


def stat_opts(color_mode="value"):
    return {
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": color_mode,
            "graphMode": "none",
            "textMode": "value",
        }
    }


GREEN_RED = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
RED_GREEN = [{"color": "red", "value": None}, {"color": "green", "value": 1}]

# ---------------------------------------------------------------- Infra (host dropdown)
host = 'host_name=~"$host"'
infra = dashboard(
    "ratp-infra",
    "RATP Infra",
    ["infra"],
    [
        panel(
            "CPU utilization",
            "timeseries",
            0,
            0,
            12,
            8,
            [
                prom(
                    f'1 - avg by (host_name) (system_cpu_utilization_ratio{{{host}, state="idle"}})',
                    "{{host_name}}",
                )
            ],
            unit="percentunit",
            description="Share of all cores busy, per host (hostmetrics)",
        ),
        panel(
            "Memory utilization",
            "timeseries",
            12,
            0,
            12,
            8,
            [
                prom(
                    f'system_memory_utilization_ratio{{{host}, state="used"}}', "{{host_name}} used"
                )
            ],
            unit="percentunit",
            description="t4g.small has 2 GiB: sustained > 80% means upsize to t4g.medium (plan 1.4)",
            thresholds=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 0.8},
                {"color": "red", "value": 0.9},
            ],
        ),
        panel(
            "Disk utilization (/)",
            "timeseries",
            0,
            8,
            8,
            8,
            [
                prom(
                    f'max by (host_name, device) (system_filesystem_utilization_ratio{{{host}, type=~"ext4|xfs"}})',
                    "{{host_name}} {{device}}",
                )
            ],
            unit="percentunit",
        ),
        panel(
            "Load average (1m)",
            "timeseries",
            8,
            8,
            8,
            8,
            [prom(f"system_cpu_load_average_1m{{{host}}}", "{{host_name}}")],
        ),
        panel(
            "Network (bytes/s)",
            "timeseries",
            16,
            8,
            8,
            8,
            [
                prom(
                    f'sum by (host_name, direction) (rate(system_network_io_bytes_total{{{host}, device!="lo"}}[5m]))',
                    "{{host_name}} {{direction}}",
                )
            ],
            unit="Bps",
        ),
        panel(
            "Container memory",
            "timeseries",
            0,
            16,
            12,
            8,
            [prom(f"container_memory_usage_total_bytes{{{host}}}", "{{container_name}}")],
            unit="bytes",
            description="Per container (docker_stats). Postgres, LGTM, app and scheduler share 2 GiB.",
        ),
        panel(
            "Container CPU",
            "timeseries",
            12,
            16,
            12,
            8,
            [prom(f"container_cpu_utilization_ratio{{{host}}}", "{{container_name}}")],
            unit="percentunit",
        ),
    ],
    [var("host", "label_values(system_memory_usage_bytes, host_name)", label="Host")],
    time_from="now-6h",
)

# ---------------------------------------------------------------- Trading ops
env = 'deployment_environment=~"$environment"'
ops = dashboard(
    "ratp-trading-ops",
    "RATP Trading Ops",
    ["trading", "ops"],
    [
        panel(
            "Scheduler heartbeat age",
            "stat",
            0,
            0,
            4,
            4,
            [
                prom(
                    f"time() - max(ratp_scheduler_heartbeat_timestamp_seconds{{{env}}})",
                    instant=True,
                )
            ],
            unit="s",
            thresholds=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 120},
                {"color": "red", "value": 600},
            ],
            extra=stat_opts("background"),
            description="Seconds since the scheduler loop last iterated. Alert at 600.",
        ),
        panel(
            "Market open",
            "stat",
            4,
            0,
            4,
            4,
            [prom(f"max(ratp_market_open{{{env}}})", instant=True)],
            thresholds=[{"color": "blue", "value": None}, {"color": "green", "value": 1}],
            extra={
                "options": {**stat_opts("background")["options"], "mappings": []},
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {"0": {"text": "closed"}, "1": {"text": "OPEN"}},
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "blue", "value": None},
                                {"color": "green", "value": 1},
                            ],
                        },
                    },
                    "overrides": [],
                },
            },
        ),
        panel(
            "Last intraday tick age",
            "stat",
            8,
            0,
            4,
            4,
            [
                prom(
                    f"time() - max(ratp_trading_cycle_last_success_timestamp_seconds{{{env}}})",
                    instant=True,
                )
            ],
            unit="s",
            thresholds=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 600},
                {"color": "red", "value": 900},
            ],
            extra=stat_opts("background"),
            description="Alert fires at 900 s while the market is open.",
        ),
        panel(
            "EOD chain overdue",
            "stat",
            12,
            0,
            4,
            4,
            [prom(f"max(ratp_eod_chain_overdue{{{env}}})", instant=True)],
            thresholds=GREEN_RED,
            extra=stat_opts("background"),
            description="1 after 19:00 ET on a trading day until the chain completes",
        ),
        panel(
            "EOD chain last completed",
            "stat",
            16,
            0,
            8,
            4,
            [
                prom(
                    f"max(ratp_eod_chain_completed_timestamp_seconds{{{env}}}) * 1000", instant=True
                )
            ],
            unit="dateTimeAsIso",
            extra=stat_opts(),
        ),
        panel(
            "Trading cycle runs by status",
            "timeseries",
            0,
            4,
            12,
            8,
            [
                prom(
                    f"sum by (status) (increase(ratp_trading_cycle_runs_total{{{env}}}[15m]))",
                    "{{status}}",
                )
            ],
        ),
        panel(
            "Trading cycle failures / degraded",
            "timeseries",
            12,
            4,
            12,
            8,
            [
                prom(
                    f"sum by (failure_class) (increase(ratp_trading_cycle_failures_total{{{env}}}[15m]))",
                    "failed {{failure_class}}",
                ),
                prom(
                    f"sum (increase(ratp_trading_cycle_degraded_total{{{env}}}[15m]))",
                    "degraded",
                    ref="B",
                ),
            ],
        ),
        panel(
            "Cycle duration p95",
            "timeseries",
            0,
            12,
            12,
            8,
            [
                prom(
                    f"histogram_quantile(0.95, sum by (le) (rate(ratp_trading_cycle_duration_seconds_bucket{{{env}}}[15m])))",
                    "p95",
                )
            ],
            unit="s",
        ),
        panel(
            "Broker API error rate",
            "timeseries",
            12,
            12,
            12,
            8,
            [
                prom(
                    f"sum by (endpoint) (rate(ratp_broker_api_failures_total{{{env}}}[5m])) / clamp_min(sum by (endpoint) (rate(ratp_broker_api_requests_total{{{env}}}[5m])), 0.001)",
                    "{{endpoint}}",
                )
            ],
            unit="percentunit",
        ),
        panel(
            "Intraday ticks today",
            "table",
            0,
            20,
            24,
            9,
            [
                sql(
                    "SELECT started_at, status, duration_ms, error_message, correlation_id FROM runtime_job_runs WHERE job_name = 'paper_trading_intraday_tick' AND $__timeFilter(started_at) ORDER BY started_at DESC LIMIT 200"
                )
            ],
            datasource=PG,
            description="One row per intraday tick (ingestion → features → trading)",
        ),
    ],
    [
        var(
            "environment",
            "label_values(ratp_scheduler_heartbeat_timestamp_seconds, deployment_environment)",
            label="Environment",
        )
    ],
    time_from="now-12h",
)

# ---------------------------------------------------------------- End-of-day pipeline
eod = dashboard(
    "ratp-eod-pipeline",
    "RATP End-of-Day Pipeline",
    ["eod", "scheduler"],
    [
        panel(
            "Chain runs (one row per start; a date can have several after a restart)",
            "table",
            0,
            0,
            24,
            8,
            [
                sql(
                    "SELECT input_summary_json->>'trading_date' AS trading_date, started_at, completed_at, status, duration_ms, error_message, jsonb_array_length(coalesce(output_summary_json->'failed_steps', '[]'::jsonb)) AS failed_steps, output_summary_json->'resumed_steps' AS resumed_steps, correlation_id FROM runtime_job_runs WHERE job_name = 'paper_trading_eod_maintenance' AND $__timeFilter(started_at) ORDER BY started_at DESC LIMIT 60"
                )
            ],
            datasource=PG,
        ),
        panel(
            "Steps per trading date (latest outcome per step)",
            "table",
            0,
            8,
            24,
            12,
            [
                sql("""WITH parents AS (
  SELECT job_run_id, input_summary_json->>'trading_date' AS trading_date
  FROM runtime_job_runs
  WHERE job_name = 'paper_trading_eod_maintenance' AND $__timeFilter(started_at)
), steps AS (
  SELECT p.trading_date, s.step_name, s.status, s.duration_ms, s.error_message, s.completed_at, s.sequence_number,
         row_number() OVER (PARTITION BY p.trading_date, s.step_name ORDER BY s.completed_at DESC) AS rn
  FROM runtime_job_run_steps s JOIN parents p ON p.job_run_id = s.job_run_id
)
SELECT trading_date, sequence_number, step_name, status, duration_ms, error_message, completed_at
FROM steps WHERE rn = 1
ORDER BY trading_date DESC, sequence_number""")
            ],
            datasource=PG,
            description="completed / failed / skipped / interrupted per step. A step skipped with 'blocked by …' sits behind a failed blocking step.",
        ),
        panel(
            "Step duration (seconds), last 30 chain runs",
            "barchart",
            0,
            20,
            12,
            9,
            [
                sql(
                    "SELECT s.step_name, round(avg(s.duration_ms) / 1000.0, 1) AS avg_s, round(max(s.duration_ms) / 1000.0, 1) AS max_s FROM runtime_job_run_steps s JOIN runtime_job_runs r ON r.job_run_id = s.job_run_id WHERE r.job_name = 'paper_trading_eod_maintenance' AND s.status = 'completed' AND $__timeFilter(r.started_at) GROUP BY s.step_name ORDER BY avg_s DESC"
                )
            ],
            datasource=PG,
            extra={"options": {"orientation": "horizontal", "xField": "step_name"}},
        ),
        panel(
            "Step attempts and failures",
            "table",
            12,
            20,
            12,
            9,
            [
                sql(
                    "SELECT replace(job_name, 'paper_trading_eod_maintenance.', '') AS step, count(*) AS attempts, count(*) FILTER (WHERE status = 'failed') AS failed_attempts, max(started_at) FILTER (WHERE status = 'failed') AS last_failure, max(error_message) FILTER (WHERE status = 'failed') AS last_error FROM runtime_job_runs WHERE job_name LIKE 'paper_trading_eod_maintenance.%' AND $__timeFilter(started_at) GROUP BY job_name ORDER BY failed_attempts DESC, step"
                )
            ],
            datasource=PG,
            description="Every retry is one attempt row",
        ),
        panel(
            "Published dataset versions (publish_datasets step output)",
            "table",
            0,
            29,
            24,
            7,
            [
                sql(
                    "SELECT started_at, status, output_summary_json->>'published' AS published, output_summary_json->>'files_uploaded' AS files_uploaded, output_summary_json->>'bytes_uploaded' AS bytes_uploaded, output_summary_json->>'index_key' AS index_key FROM runtime_job_runs WHERE job_name = 'paper_trading_eod_maintenance.publish_datasets' AND $__timeFilter(started_at) ORDER BY started_at DESC LIMIT 30"
                )
            ],
            datasource=PG,
        ),
    ],
    [],
    time_from="now-14d",
    refresh="1m",
)

for name, d in (("infra.json", infra), ("trading-ops.json", ops), ("eod-pipeline.json", eod)):
    (OUT / name).write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    print("wrote", name, len(d["panels"]), "panels")
