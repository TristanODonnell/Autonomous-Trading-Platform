import math
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.contracts.accounting.risk_snapshot import RiskSnapshot
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.risk_snapshot import RISK_SNAPSHOT_RULES


def make_risk_snapshot(**overrides) -> RiskSnapshot:
    data = {
        "snapshot_id": uuid4(),
        "run_id": uuid4(),
        "timestamp": datetime.now(UTC),
        "gross_exposure": 100000.0,
        "net_exposure": 50000.0,
        "leverage": 1.5,
        "drawdown_pct": None,
        "limits": {"max_leverage": 3},
        "utilization": {"leverage": 1.5},
        "is_blocked": False,
        "block_reasons": None,
    }

    data.update(overrides)
    return RiskSnapshot(**data)


def test_valid_risk_snapshot_passes():
    snapshot = make_risk_snapshot()

    result = run_rules(snapshot, RISK_SNAPSHOT_RULES)

    assert result.ok
    assert result.violations == []


def test_net_exposure_must_be_finite():
    with pytest.raises(ValidationError):
        make_risk_snapshot(net_exposure=math.inf)


def test_gross_exposure_must_be_finite():
    with pytest.raises(ValidationError):
        make_risk_snapshot(gross_exposure=math.inf)


def test_leverage_must_be_finite():
    snapshot = make_risk_snapshot(leverage=math.inf)

    result = run_rules(snapshot, RISK_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "LEVERAGE_FINITE" for v in result.violations)


def test_gross_exposure_must_be_gte_abs_net_exposure():
    snapshot = make_risk_snapshot(
        gross_exposure=100,
        net_exposure=200,
    )

    result = run_rules(snapshot, RISK_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "GROSS_EXPOSURE_GTE_ABS_NET_EXPOSURE" for v in result.violations)


def test_block_reasons_required_when_blocked():
    snapshot = make_risk_snapshot(
        is_blocked=True,
        block_reasons=None,
    )

    result = run_rules(snapshot, RISK_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "BLOCK_REASONS_REQUIRED_WHEN_BLOCKED" for v in result.violations)


def test_leverage_must_be_non_negative():
    snapshot = make_risk_snapshot(leverage=-1.0)

    result = run_rules(snapshot, RISK_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "LEVERAGE_NON_NEGATIVE" for v in result.violations)
