from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import requests

from autonomous_trading_platform.application.services.worker_result_client import (
    RESULTS_PATH,
    WorkerResultClient,
    WorkerResultPublishError,
)
from autonomous_trading_platform.cli.commands import worker as worker_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.governance.worker_results import (
    WorkerResultSubmission,
    WorkerResultType,
)


class _Response:
    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class _Http:
    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict] = []

    def post(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.response


def _submission() -> WorkerResultSubmission:
    return WorkerResultSubmission(
        run_id="w1",
        result_type=WorkerResultType.BENCH_RESIMS,
        produced_at=datetime(2026, 10, 16, 23, 0, tzinfo=UTC),
        git_sha="abc1234",
        dataset_as_of=date(2026, 10, 16),
        payload={
            "window_start": "2026-07-20",
            "window_end": "2026-10-16",
            "outcomes": [{"strategy_id": "a"}],
        },
    )


def _decision(status: str, **extra) -> dict:
    return {
        "data": {
            "run_id": "w1",
            "result_type": "bench_resims",
            "status": status,
            "reason": extra.get("reason"),
            "duplicate": extra.get("duplicate", False),
            "received_at": "2026-10-16T23:00:01Z",
            "dataset_age_days": 0,
        },
        "meta": {"request_id": "r"},
    }


def test_publish_posts_json_with_bearer_and_returns_the_decision() -> None:
    http = _Http(_Response(200, _decision("accepted")))
    client = WorkerResultClient("http://10.0.0.1:8000/", "tok", session=http)  # type: ignore[arg-type]

    decision = client.publish(_submission())

    assert decision.status.value == "accepted"
    [call] = http.calls
    assert call["url"] == "http://10.0.0.1:8000" + RESULTS_PATH
    assert call["headers"] == {"Authorization": "Bearer tok"}
    assert call["json"]["run_id"] == "w1"
    assert call["json"]["dataset_as_of"] == "2026-10-16"
    assert call["json"]["produced_at"].startswith("2026-10-16T23:00:00")


def test_rejection_is_a_decision_not_an_error() -> None:
    http = _Http(_Response(200, _decision("rejected", reason="stale_dataset")))

    decision = WorkerResultClient("http://a", "t", session=http).publish(_submission())  # type: ignore[arg-type]

    assert decision.status.value == "rejected" and decision.reason == "stale_dataset"


def test_non_200_and_network_errors_raise() -> None:
    forbidden = _Http(_Response(403, {"detail": "no"}))
    client = WorkerResultClient("http://a", "t", session=forbidden)  # type: ignore[arg-type]
    with pytest.raises(WorkerResultPublishError) as info:
        client.publish(_submission())
    assert info.value.status_code == 403 and info.value.body == {"detail": "no"}

    down = _Http(exc=requests.ConnectionError("down"))
    client = WorkerResultClient("http://a", "t", session=down)  # type: ignore[arg-type]
    with pytest.raises(WorkerResultPublishError, match="unreachable"):
        client.publish(_submission())


def test_from_env_requires_both_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOX_A_API_URL", raising=False)
    monkeypatch.delenv("BOX_A_API_TOKEN", raising=False)
    with pytest.raises(WorkerResultPublishError, match="BOX_A_API_URL"):
        WorkerResultClient.from_env()

    monkeypatch.setenv("BOX_A_API_URL", "http://a:8000")
    monkeypatch.setenv("BOX_A_API_TOKEN", "t")
    assert isinstance(WorkerResultClient.from_env(), WorkerResultClient)


def test_cli_publish_result_builds_the_submission_from_summary(tmp_path: Path) -> None:
    payload = tmp_path / "bench_resims.json"
    payload.write_text(
        json.dumps({"window_start": "2026-07-20", "window_end": "2026-10-16", "outcomes": []})
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "git_sha": "deadbeef",
                "host_name": "box-b",
                "fixture": "fixtures/x.yaml",
                "datasets_as_of": "2026-10-16",
            }
        )
    )
    args = build_parser().parse_args(
        [
            "worker",
            "publish-result",
            "--run-id",
            "six_month_2026w42",
            "--type",
            "bench_resims",
            "--payload",
            str(payload),
            "--summary",
            str(summary),
            "--dataset-version",
            "raw_bars_v1",
        ]
    )
    assert args.func is worker_cmd.handle_publish_result

    submission = worker_cmd.build_submission(args)

    assert submission.run_id == "six_month_2026w42"
    assert submission.result_type is WorkerResultType.BENCH_RESIMS
    assert submission.git_sha == "deadbeef" and submission.worker_host == "box-b"
    assert submission.fixture == "fixtures/x.yaml"
    assert submission.dataset_as_of == date(2026, 10, 16)
    assert submission.dataset_versions == ["raw_bars_v1"]
    assert submission.payload["window_end"] == "2026-10-16"

    # explicit flags win over the summary
    args2 = build_parser().parse_args(
        [
            "worker",
            "publish-result",
            "--run-id",
            "r",
            "--type",
            "bench_resims",
            "--payload",
            str(payload),
            "--as-of",
            "2026-10-15",
            "--fixture",
            "f.yaml",
        ]
    )
    s2 = worker_cmd.build_submission(args2)
    assert s2.dataset_as_of == date(2026, 10, 15) and s2.fixture == "f.yaml"


def test_cli_publish_result_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"x": 1}))
    base = [
        "worker",
        "publish-result",
        "--run-id",
        "r",
        "--type",
        "bench_resims",
        "--payload",
        str(payload),
    ]

    class _Client:
        def __init__(self, status: str) -> None:
            self.status = status

        def publish(self, submission):
            from autonomous_trading_platform.contracts.governance.worker_results import (
                WorkerResultDecision,
            )

            return WorkerResultDecision(
                run_id="r",
                result_type=WorkerResultType.BENCH_RESIMS,
                status=self.status,  # type: ignore[arg-type]
                reason="stale_dataset" if self.status == "rejected" else None,
                received_at=datetime.now(UTC),
            )

    from autonomous_trading_platform.application.services import worker_result_client as mod

    monkeypatch.setattr(
        mod.WorkerResultClient, "from_env", classmethod(lambda cls: _Client("accepted"))
    )
    assert worker_cmd.handle_publish_result(build_parser().parse_args(base)) == 0
    monkeypatch.setattr(
        mod.WorkerResultClient, "from_env", classmethod(lambda cls: _Client("rejected"))
    )
    assert worker_cmd.handle_publish_result(build_parser().parse_args(base)) == 2

    def boom(cls):
        raise WorkerResultPublishError("Box A answered 403", status_code=403, body={"detail": "no"})

    monkeypatch.setattr(mod.WorkerResultClient, "from_env", classmethod(boom))
    assert worker_cmd.handle_publish_result(build_parser().parse_args(base)) == 1
    assert isinstance(argparse.Namespace(), argparse.Namespace)
