from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

DASHBOARD_DIR = Path("infra/observability/grafana/dashboards")
PROVISIONING_DIR = Path("infra/observability/grafana/provisioning")

EXPECTED_DASHBOARDS = {
    "runtime-health.json",
    "governance-runtime.json",
    "allocation-rebalance.json",
    "broker-operations.json",
    "reconciliation.json",
    "runtime-verification.json",
}

BANNED_PROM_LABEL_RE = re.compile(
    r"ratp_[a-zA-Z0-9_:]+[^{]*\{[^}]*(run_id|correlation_id|job_run_id|order_id|symbol|dataset_version_id)\s*="
)


def test_grafana_dashboard_json_files_are_provisioned_and_parseable() -> None:
    files = {path.name for path in DASHBOARD_DIR.glob("*.json")}

    assert files == EXPECTED_DASHBOARDS

    for path in DASHBOARD_DIR.glob("*.json"):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        assert dashboard["uid"].startswith("ratp-")
        assert dashboard["panels"]
        assert any(link["title"].startswith("Tempo") for link in dashboard["links"])
        assert any(link["title"].startswith("Loki") for link in dashboard["links"])


def test_grafana_datasources_include_prometheus_loki_tempo_and_postgres() -> None:
    config = yaml.safe_load(
        (PROVISIONING_DIR / "datasources" / "datasources.yaml").read_text(encoding="utf-8")
    )
    uids = {datasource["uid"] for datasource in config["datasources"]}

    assert {"prometheus", "loki", "tempo", "ratp-postgres"}.issubset(uids)


def test_dashboard_prometheus_queries_do_not_use_high_cardinality_labels() -> None:
    for path in DASHBOARD_DIR.glob("*.json"):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                datasource = target.get("datasource", {})
                if datasource.get("uid") != "prometheus":
                    continue
                expr = target.get("expr", "")
                assert not BANNED_PROM_LABEL_RE.search(expr), (path.name, panel["title"], expr)
