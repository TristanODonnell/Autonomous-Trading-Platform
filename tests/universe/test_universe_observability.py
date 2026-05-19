from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.universe.services.universe_rebalance_service import (
    UniverseRebalanceService,
)
from autonomous_trading_platform.universe.services.universe_rotation_service import (
    UniverseRotationService,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig
from tests.universe.test_universe_candidate_builder import (
    _bars_for_symbol,
    _default_config,
    _make_builder,
)


def test_metrics_emitted_during_candidate_generation(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.services.universe_candidate_builder."
        "record_universe_candidate_generation_metrics",
        lambda **kwargs: calls.append(kwargs),
    )

    builder = _make_builder(
        symbols=["AAPL", "NO_DATA"],
        bars_by_symbol={"AAPL": _bars_for_symbol("AAPL", close="50.00", volume=500_000)},
    )
    builder.build_candidate(config=_default_config(), name="observability")

    assert calls
    assert calls[0]["candidate_count"] == 2
    assert calls[0]["rejected_count"] == 1
    assert calls[0]["ranking_duration_seconds"] >= 0


def test_structured_log_fields_present_for_candidate_generation(caplog) -> None:
    builder = _make_builder(
        symbols=["AAPL"],
        bars_by_symbol={"AAPL": _bars_for_symbol("AAPL", close="50.00", volume=500_000)},
    )

    with caplog.at_level("INFO"):
        result = builder.build_candidate(config=_default_config(), name="observability")

    completed = [
        record for record in caplog.records if record.message == "candidate generation completed"
    ][0]
    assert completed.universe_version_id == result.version.universe_version_id
    assert completed.candidate_universe_version_id == result.version.universe_version_id
    assert completed.step == "candidate_generation_completed"


def test_metrics_emitted_during_rebalance(db_session, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.services.universe_rebalance_service."
        "record_universe_rebalance_metrics",
        lambda **kwargs: calls.append(kwargs),
    )
    candidate = _insert_candidate(db_session, ["AAPL", "MSFT"])

    result = UniverseRebalanceService(db_session).propose_rebalance(
        active_version_id=None,
        candidate_version_id=candidate.universe_version_id,
        config=UniverseRebalanceConfig(
            target_universe_size=2,
            allow_empty_active_universe_bootstrap=True,
        ),
    )

    assert calls
    assert calls[0]["added_count"] == len(result.added)
    assert calls[0]["removed_count"] == 0
    assert calls[0]["retained_count"] == 0


def test_metrics_emitted_during_rotation(db_session, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.services.universe_rotation_service."
        "record_universe_rotation_metrics",
        lambda **kwargs: calls.append(kwargs),
    )
    candidate = _insert_candidate(db_session, ["AAPL", "MSFT"])

    result = UniverseRotationService(db_session).rotate(
        candidate_version_id=candidate.universe_version_id,
        config=UniverseRebalanceConfig(
            target_universe_size=2,
            allow_empty_active_universe_bootstrap=True,
        ),
        force_rotation=True,
        as_of=datetime(2025, 1, 15, tzinfo=UTC),
    )

    assert calls
    assert calls[0]["rotation_type"] == "scheduled"
    assert result.rebalance_result is not None
    assert calls[0]["added_count"] == len(result.rebalance_result.added)


def test_validation_catches_invalid_active_universe_state(db_session) -> None:
    from autonomous_trading_platform.universe.services.universe_validation_service import (
        UniverseValidationService,
    )

    as_of = datetime(2025, 1, 15, tzinfo=UTC)
    _insert_active(db_session, "active-a", ["AAPL"], as_of=as_of)
    _insert_active(db_session, "active-b", ["MSFT"], as_of=as_of)

    result = UniverseValidationService(db_session).validate_active(as_of)

    assert result.ok is False
    assert any("Multiple active universes" in error for error in result.errors)


def _insert_candidate(db_session, symbols: list[str]) -> UniverseVersion:
    now = datetime(2025, 1, 15, tzinfo=UTC)
    version = UniverseVersion(
        universe_version_id=f"candidate-{len(symbols)}-{symbols[0]}",
        name="candidate",
        source=UniverseSource.CUSTOM,
        created_at=now,
        effective_from=now,
        effective_to=None,
        status=UniverseStatus.CANDIDATE,
        rebalance_reason=None,
        config_hash="candidate-hash-" + symbols[0],
        generation_metadata_json={"included_count": len(symbols)},
    )
    db_session.add(version)
    db_session.add_all(
        [
            UniverseMember(
                universe_version_id=version.universe_version_id,
                symbol=symbol,
                rank=index,
                score=1.0 / index,
                included_reason="test",
                excluded_reason=None,
                liquidity_metrics_json={
                    "last_price": str(Decimal("100.00")),
                    "average_daily_dollar_volume": str(Decimal("1000000")),
                },
                quality_metrics_json={"history_days": 20},
            )
            for index, symbol in enumerate(symbols, start=1)
        ]
    )
    db_session.flush()
    return version


def _insert_active(db_session, version_id: str, symbols: list[str], *, as_of: datetime) -> None:
    version = UniverseVersion(
        universe_version_id=version_id,
        name=version_id,
        source=UniverseSource.CUSTOM,
        created_at=as_of,
        effective_from=as_of,
        effective_to=None,
        status=UniverseStatus.ACTIVE,
        rebalance_reason=None,
        config_hash=version_id + "-hash",
        generation_metadata_json={"included_count": len(symbols)},
    )
    db_session.add(version)
    db_session.add_all(
        [
            UniverseMember(
                universe_version_id=version_id,
                symbol=symbol,
                rank=index,
                score=1.0,
                included_reason="test",
                excluded_reason=None,
                liquidity_metrics_json=None,
                quality_metrics_json=None,
            )
            for index, symbol in enumerate(symbols, start=1)
        ]
    )
    db_session.flush()
