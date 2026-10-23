from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from tests.conftest import auth_headers

URL = "/api/v1/worker/results"


def _body(**overrides) -> dict:
    today = datetime.now(UTC).date()
    base = {
        "run_id": "six_month_2026w42",
        "result_type": "bench_resims",
        "produced_at": datetime.now(UTC).isoformat(),
        "git_sha": "abc1234",
        "worker_host": "box-b",
        "fixture": "fixtures/platform/replays/long/six_month_regression.yaml",
        "dataset_as_of": (today - timedelta(days=1)).isoformat(),
        "dataset_versions": ["raw_bars_v1"],
        "payload": {
            "window_start": (today - timedelta(days=90)).isoformat(),
            "window_end": (today - timedelta(days=1)).isoformat(),
            "outcomes": [{"strategy_id": "momentum_v1", "score": "1.2", "daily_returns": {}}],
        },
    }
    base.update(overrides)
    return base


def _enable(session: Session) -> None:
    OperatorSettingsRepository(session).update_current(
        {"worker_results_enabled": True, "worker_result_require_matching_git_sha": False},
        updated_by="test",
    )


def test_worker_role_can_post_and_gets_a_decision(client: TestClient, db_session: Session) -> None:
    _enable(db_session)

    response = client.post(URL, json=_body(), headers=auth_headers(role="worker"))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "accepted"
    assert data["reason"] is None
    assert data["duplicate"] is False
    assert data["dataset_age_days"] == 1

    # listed for operators, with the payload left out
    listed = client.get(URL, headers=auth_headers(role="operator"))
    assert listed.status_code == 200
    [record] = listed.json()["data"]
    assert record["run_id"] == "six_month_2026w42"
    assert record["status"] == "accepted"
    assert record["worker_host"] == "box-b"
    assert "payload" not in record

    one = client.get(f"{URL}/six_month_2026w42", headers=auth_headers(role="admin"))
    assert one.status_code == 200
    assert one.json()["data"]["dataset_versions"] == ["raw_bars_v1"]


def test_rejections_come_back_as_200_with_the_reason(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(URL, json=_body(), headers=auth_headers(role="worker"))

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"
    assert response.json()["data"]["reason"] == "worker_results_disabled"

    _enable(db_session)
    KillSwitchStateRepository(db_session).enable(
        reason="x", updated_by="ops", updated_at=datetime.now(UTC)
    )
    killed = client.post(URL, json=_body(run_id="r2"), headers=auth_headers(role="worker"))
    assert killed.json()["data"]["reason"] == "kill_switch_enabled"


def test_same_run_id_is_idempotent(client: TestClient, db_session: Session) -> None:
    _enable(db_session)
    first = client.post(URL, json=_body(), headers=auth_headers(role="worker")).json()["data"]

    again = client.post(URL, json=_body(), headers=auth_headers(role="worker")).json()["data"]

    assert first["duplicate"] is False
    assert again["duplicate"] is True
    assert again["status"] == first["status"] == "accepted"
    assert len(client.get(URL, headers=auth_headers(role="operator")).json()["data"]) == 1


def test_invalid_bench_payload_is_rejected_not_500(client: TestClient, db_session: Session) -> None:
    _enable(db_session)

    response = client.post(
        URL,
        json=_body(payload={"window_start": "2026-01-01", "outcomes": []}),
        headers=auth_headers(role="worker"),
    )

    assert response.status_code == 200
    assert response.json()["data"]["reason"] == "invalid_payload"


def test_malformed_submission_is_422(client: TestClient) -> None:
    response = client.post(URL, json={"run_id": ""}, headers=auth_headers(role="worker"))

    assert response.status_code == 422


def test_roles(client: TestClient, db_session: Session) -> None:
    _enable(db_session)
    for role in ("operator", "researcher", "risk_manager"):
        denied = client.post(URL, json=_body(run_id=role), headers=auth_headers(role=role))
        assert denied.status_code == 403, role
    assert (
        client.post(URL, json=_body(run_id="adm"), headers=auth_headers(role="admin")).status_code
        == 200
    )
    # the worker may post but not browse
    assert client.get(URL, headers=auth_headers(role="worker")).status_code == 403
    assert client.get(f"{URL}/nope", headers=auth_headers(role="operator")).status_code == 404
    assert client.post(URL, json=_body()).status_code in (401, 403)


def test_stale_dataset_is_rejected(client: TestClient, db_session: Session) -> None:
    _enable(db_session)
    old = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()

    response = client.post(URL, json=_body(dataset_as_of=old), headers=auth_headers(role="worker"))

    assert response.json()["data"]["reason"] == "stale_dataset"
    assert response.json()["data"]["dataset_age_days"] == 10
    assert date.fromisoformat(old)  # sanity


# ---------------------------------------------------------------- bench inputs (3.3a)


def test_worker_role_fetches_bench_inputs(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    from autonomous_trading_platform.application.services import bench_resim_inputs_service
    from autonomous_trading_platform.application.services.bench_resimulation_service import (
        BenchWindow,
    )
    from autonomous_trading_platform.contracts.common.enums import PriceBasis
    from tests.application.services.test_bench_resim_inputs_service import (
        seed_tracked_strategy,
    )

    seed_tracked_strategy(db_session, "momentum_v1", tier="active")
    seed_tracked_strategy(
        db_session, "macd_crossover_v1", tier="bench", strategy_type="moving_average_crossover"
    )
    monkeypatch.setattr(
        bench_resim_inputs_service,
        "resolve_bench_window",
        lambda **kwargs: BenchWindow(
            dataset_version="raw_bars_x",
            price_basis=PriceBasis.RAW,
            symbols=["AAPL"],
            start_date=kwargs["as_of"] - timedelta(days=88),
            end_date=kwargs["as_of"],
        ),
    )

    response = client.get(
        "/api/v1/worker/bench-inputs",
        params={"as_of": "2026-10-23", "trading_days": 63},
        headers=auth_headers(role="worker"),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["as_of"] == "2026-10-23"
    assert data["trading_days"] == 63
    assert data["window_start"] == "2026-07-27"
    assert data["window_end"] == "2026-10-23"
    assert data["dataset_version"] == "raw_bars_x"
    assert data["symbols"] == ["AAPL"]
    assert [(s["strategy_id"], s["tier"]) for s in data["strategies"]] == [
        ("macd_crossover_v1", "bench"),
        ("momentum_v1", "active"),
    ]
    assert data["strategies"][1]["parameters"] == {"lookback": 5}


def test_bench_inputs_default_to_today_and_need_the_worker_role(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    from autonomous_trading_platform.application.services import bench_resim_inputs_service

    monkeypatch.setattr(bench_resim_inputs_service, "resolve_bench_window", lambda **kw: None)

    forbidden = client.get("/api/v1/worker/bench-inputs", headers=auth_headers(role="operator"))
    assert forbidden.status_code == 403

    response = client.get("/api/v1/worker/bench-inputs", headers=auth_headers(role="admin"))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["as_of"] == datetime.now(UTC).date().isoformat()
    assert data["strategies"] == []
    assert data["window_reason"] == "insufficient_data_for_window"

    assert (
        client.get(
            "/api/v1/worker/bench-inputs",
            params={"trading_days": 1},
            headers=auth_headers("worker"),
        ).status_code
        == 422
    )
