# Alerting and operator response

## Severity taxonomy

- `info`: informational operational events, expected state transitions, and successful recovery events.
- `warning`: degraded but recoverable runtime state, elevated latency, retry spikes, delayed ingestion, and stale jobs.
- `critical`: trading halt conditions, broker unavailability, reconciliation drift beyond critical thresholds, runtime cycle failures, freeze/kill-switch activation, and unrecoverable operational failures.

## Provisioning

Prometheus alert rules are provisioned from `infra/observability/prometheus/alerts/ratp-alerts.yaml`.
`docker-compose.yml` mounts that directory into the LGTM container at `/otel-lgtm/alerts`, and `infra/observability/prometheus-config.yaml` loads `/otel-lgtm/alerts/*.yaml`.

Alert rules use existing aggregate metrics only. They do not add `run_id`, `correlation_id`, `order_id`, or `symbol` as Prometheus labels.

## Operator response state

Operator alert state is persisted in `operational_alerts` and exposed through:

- `GET /api/v1/operations/alerts`
- `POST /api/v1/operations/alerts`
- `POST /api/v1/operations/alerts/{alert_id}/acknowledge`
- `POST /api/v1/operations/alerts/{alert_id}/resolve`
- `POST /api/v1/operations/alerts/{alert_id}/snooze`
- `POST /api/v1/operations/alerts/{alert_id}/unsnooze`
- `POST /api/v1/operations/alerts/{alert_id}/notes`

All operator actions generate audit events through the existing audit log infrastructure.

## Control linkage

Alert records can carry a `recommended_action` such as trading freeze, strategy pause, kill switch, or broker disablement. These recommendations are advisory unless an explicit operator-approved policy is added later. The API does not auto-trigger destructive controls.
