"""Admin domain replay hook (P1 — preflight only)."""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.platform_replay import (
    AdminPreflightResult,
    AdminPreflightSummary,
    PlatformReplayContext,
)


def validate_admin_preflight(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> AdminPreflightResult:
    """Read-only DB and config validation. Called before replay starts."""
    base = dict(
        domain="admin",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    warnings: list[str] = []
    errors: list[str] = []
    db_ok = False
    config_ok = False
    alembic_version: str | None = None

    # Config check
    try:
        from autonomous_trading_platform.config.settings import Settings

        Settings()
        config_ok = True
    except Exception as exc:
        errors.append(f"Settings instantiation failed: {exc}")

    # DB connectivity + alembic version
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
        try:
            row = session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).fetchone()
            alembic_version = row[0] if row else "<no rows>"
        except Exception:
            warnings.append("Alembic version table not found (advisory)")
            alembic_version = "<not found>"
    except Exception as exc:
        errors.append(f"DB connectivity failed: {exc}")

    # NO_LIVE_TRADING guard
    no_live = os.getenv("NO_LIVE_TRADING", "false").lower()
    if no_live not in ("true", "1", "yes"):
        warnings.append("NO_LIVE_TRADING is not set — ensure replay runs in non-live environment")

    status = "ok" if db_ok and config_ok else "failed"

    return AdminPreflightResult(
        **base,
        status=status,
        warnings=warnings,
        errors=errors,
        db_ok=db_ok,
        config_ok=config_ok,
        alembic_version=alembic_version,
        summary={
            "db_ok": db_ok,
            "config_ok": config_ok,
            "alembic_version": alembic_version,
        },
    )


def build_admin_preflight_summary(*, session: Session) -> AdminPreflightSummary:
    """Build preflight summary for the platform artifact bundle."""
    db_ok = False
    alembic_version: str | None = None
    config_ok = False

    try:
        session.execute(text("SELECT 1"))
        db_ok = True
        row = session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
        alembic_version = row[0] if row else None
    except Exception:
        pass

    try:
        from autonomous_trading_platform.config.settings import Settings

        Settings()
        config_ok = True
    except Exception:
        pass

    return AdminPreflightSummary(
        db_ok=db_ok,
        config_ok=config_ok,
        alembic_version=alembic_version,
    )
