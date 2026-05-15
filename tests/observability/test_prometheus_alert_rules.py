from __future__ import annotations

import re
from pathlib import Path

import yaml

ALERT_RULES = Path("infra/observability/prometheus/alerts/ratp-alerts.yaml")
PROM_CONFIG = Path("infra/observability/prometheus-config.yaml")
DOCKER_COMPOSE = Path("docker-compose.yml")

BANNED_PROM_LABEL_RE = re.compile(
    r"ratp_[a-zA-Z0-9_:]+[^{]*\{[^}]*(run_id|correlation_id|job_run_id|order_id|symbol)\s*="
)


def test_prometheus_alert_rules_parse_and_use_severity_taxonomy() -> None:
    config = yaml.safe_load(ALERT_RULES.read_text(encoding="utf-8"))
    alerts = [rule for group in config["groups"] for rule in group["rules"]]

    assert {rule["labels"]["severity"] for rule in alerts}.issubset({"info", "warning", "critical"})
    assert {rule["labels"]["category"] for rule in alerts}.issubset(
        {"runtime", "broker", "reconciliation", "risk_governance", "control_infra"}
    )
    assert len(alerts) >= 10


def test_prometheus_alert_rules_avoid_banned_high_cardinality_metric_labels() -> None:
    config = yaml.safe_load(ALERT_RULES.read_text(encoding="utf-8"))
    for group in config["groups"]:
        for rule in group["rules"]:
            assert not BANNED_PROM_LABEL_RE.search(rule["expr"]), rule["alert"]
            for banned in ("run_id", "correlation_id", "order_id", "symbol"):
                assert banned not in rule.get("labels", {})


def test_prometheus_alert_rules_are_loaded_by_compose_provisioning() -> None:
    prom_config = yaml.safe_load(PROM_CONFIG.read_text(encoding="utf-8"))
    compose = yaml.safe_load(DOCKER_COMPOSE.read_text(encoding="utf-8"))
    lgtm_volumes = compose["services"]["lgtm"]["volumes"]

    assert "/otel-lgtm/alerts/*.yaml" in prom_config["rule_files"]
    assert any(
        "infra/observability/prometheus/alerts:/otel-lgtm/alerts:ro" in volume
        for volume in lgtm_volumes
    )
