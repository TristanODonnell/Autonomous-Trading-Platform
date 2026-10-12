from __future__ import annotations

import os
import subprocess
import sys

import jwt


def _mint(*args: str) -> dict:
    out = subprocess.run(
        [sys.executable, "scripts/generate_dev_jwt.py", *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "JWT_SECRET": "test-secret"},
    ).stdout.strip()
    claims: dict = jwt.decode(out, "test-secret", algorithms=["HS256"])
    return claims


def test_worker_token_carries_role_sub_and_a_unique_stamp() -> None:
    first = _mint("--role", "worker", "--sub", "box-b")
    second = _mint("--role", "worker", "--sub", "box-b")

    assert first["role"] == "worker" and first["sub"] == "box-b"
    assert "roles" not in first
    assert first["jti"] != second["jti"]
    assert isinstance(first["iat"], int)


def test_admin_default_keeps_the_legacy_roles_claim() -> None:
    claims = _mint()

    assert claims["role"] == "admin"
    assert claims["roles"] == ["admin", "operator", "risk_manager"]
    assert claims["sub"] == "local-dev-admin"
