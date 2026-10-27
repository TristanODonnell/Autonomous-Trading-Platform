"""The worker's side of the hand-off: post a result to Box A's API (plan 3.2).

``POST {base_url}/api/v1/worker/results`` with a bearer token carrying the ``worker``
role. Box A always answers a decision (accepted / rejected / duplicate) with HTTP 200;
anything else (401/403, 422 validation, 5xx, network) raises ``WorkerResultPublishError``
so a job's summary records the failure instead of silently moving on.

``GET {base_url}/api/v1/worker/bench-inputs`` (plan 3.3a) fetches what A wants
re-simulated this week; the same error rule applies.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import requests

from autonomous_trading_platform.contracts.governance.worker_results import (
    BenchResimInputs,
    WorkerResultDecision,
    WorkerResultSubmission,
)

RESULTS_PATH = "/api/v1/worker/results"
BENCH_INPUTS_PATH = "/api/v1/worker/bench-inputs"


class WorkerResultPublishError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class WorkerResultClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._http = session or requests.Session()

    @classmethod
    def from_env(cls) -> WorkerResultClient:
        base_url = os.getenv("BOX_A_API_URL") or ""
        token = os.getenv("BOX_A_API_TOKEN") or ""
        if not base_url or not token:
            raise WorkerResultPublishError("BOX_A_API_URL and BOX_A_API_TOKEN must be set")
        return cls(base_url, token)

    def publish(self, submission: WorkerResultSubmission) -> WorkerResultDecision:
        try:
            response = self._http.post(
                f"{self._base_url}{RESULTS_PATH}",
                json=submission.model_dump(mode="json"),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise WorkerResultPublishError(f"Box A unreachable: {exc}") from exc
        data = self._data(response, what=submission.run_id)
        decision: WorkerResultDecision = WorkerResultDecision.model_validate(data)
        return decision

    def fetch_bench_inputs(
        self, *, as_of: date | None = None, trading_days: int | None = None
    ) -> BenchResimInputs:
        params: dict[str, Any] = {}
        if as_of is not None:
            params["as_of"] = as_of.isoformat()
        if trading_days is not None:
            params["trading_days"] = trading_days
        try:
            response = self._http.get(
                f"{self._base_url}{BENCH_INPUTS_PATH}",
                params=params,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise WorkerResultPublishError(f"Box A unreachable: {exc}") from exc
        data = self._data(response, what="bench-inputs")
        inputs: BenchResimInputs = BenchResimInputs.model_validate(data)
        return inputs

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _data(response: Any, *, what: str) -> Any:
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code != 200:
            raise WorkerResultPublishError(
                f"Box A answered {response.status_code} for {what}",
                status_code=response.status_code,
                body=body,
            )
        return body.get("data", body) if isinstance(body, dict) else body
