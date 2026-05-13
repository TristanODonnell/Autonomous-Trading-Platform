from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_admin,
    require_operator_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.operator_settings_service import (
    OperatorSettingsService,
)
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.settings_schema import (
    AdvancedSettingsResponse,
    AssetPositionCapResponse,
    CostModelConfigurationResponse,
    OperatorSettingsResponse,
    OperatorSettingsUpdateRequest,
    RiskProfileResponse,
    StrategyDrawdownOverrideResponse,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


def _service(session: Session) -> OperatorSettingsService:
    return OperatorSettingsService(
        settings_repo=OperatorSettingsRepository(session),
        audit_log_repo=AuditLogRepository(session),
    )


def _settings_response(result) -> OperatorSettingsResponse:
    payload = dict(result.__dict__)
    payload["metadata"] = {
        "source_of_truth": {
            "automation_controls_source": "settings",
            "promotion_thresholds_source": "promotion_rules",
            "allocation_targets_source": ("capital_allocation_policies + allocation_overrides"),
        },
        "automation_controls": {
            "auto_promote_enabled": {
                "value": result.auto_promote_enabled,
                "label": "Auto Promote",
                "description": (
                    "Allows eligible strategies to be promoted automatically based "
                    "on active Promotion Rules."
                ),
                "source": "settings",
            },
            "auto_demote_on_breach": {
                "value": result.auto_demote_on_breach,
                "label": "Auto Demote",
                "description": (
                    "Allows strategies to be demoted automatically when active rules are breached."
                ),
                "source": "settings",
            },
            "auto_rebalance_enabled": {
                "value": result.auto_rebalance_enabled,
                "label": "Auto Rebalance",
                "description": (
                    "Allows allocation changes based on strategy quality and risk signals."
                ),
                "source": "settings",
            },
            "rebalance_frequency": {
                "value": result.rebalance_frequency,
                "label": "Rebalance Frequency",
                "description": ("How often the automated allocation review should run."),
                "source": "settings",
            },
        },
        "deprecated_or_ignored_settings": {
            "min_sharpe_for_promotion": {
                "value": result.min_sharpe_for_promotion,
                "status": "deprecated_persisted_only",
                "ignored_for": "manual_promotion_eligibility",
                "active_source": "promotion_rules.min_sharpe",
            },
            "min_paper_trading_period_days": {
                "value": result.min_paper_trading_period_days,
                "status": "deprecated_persisted_only",
                "ignored_for": "manual_promotion_eligibility",
                "active_source": "promotion_rules.min_days_tested",
            },
        },
    }
    return OperatorSettingsResponse(**payload)


@router.get(
    "",
    response_model=SuccessEnvelope[OperatorSettingsResponse],
)
def get_settings(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[OperatorSettingsResponse]:
    result = _service(session).get_settings()
    return success_response(
        data=_settings_response(result),
        request_id=request_id,
    )


@router.put(
    "",
    response_model=SuccessEnvelope[OperatorSettingsResponse],
)
def update_settings(
    payload: OperatorSettingsUpdateRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_admin),
) -> SuccessEnvelope[OperatorSettingsResponse]:
    updates = payload.model_dump(exclude_unset=True, exclude={"reason"})
    result = _service(session).update_settings(
        updates,
        actor_user_id=actor,
        reason=payload.reason,
    )
    return success_response(
        data=_settings_response(result),
        request_id=request_id,
    )


@router.get(
    "/risk-profile",
    response_model=SuccessEnvelope[RiskProfileResponse],
)
def get_risk_profile(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[RiskProfileResponse]:
    return success_response(
        data=RiskProfileResponse(**_service(session).get_risk_profile()),
        request_id=request_id,
    )


@router.get(
    "/advanced",
    response_model=SuccessEnvelope[AdvancedSettingsResponse],
)
def get_advanced_settings(
    request: Request,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[AdvancedSettingsResponse]:
    settings = Settings()
    role = str(getattr(request.state, "role", None) or "")
    drawdown_overrides = [
        StrategyDrawdownOverrideResponse(
            strategy_id=row.strategy_id,
            max_drawdown_allowed=Decimal(str(row.max_drawdown_allowed)),
            updated_by=row.overridden_by,
            reason=row.override_reason,
        )
        for row in session.scalars(
            select(AllocationOverrides)
            .where(AllocationOverrides.is_active.is_(True))
            .where(AllocationOverrides.max_drawdown_allowed.is_not(None))
            .order_by(AllocationOverrides.strategy_id.asc())
        )
    ]

    data = AdvancedSettingsResponse(
        read_only=role != "admin",
        per_strategy_max_drawdown_overrides=drawdown_overrides,
        position_size_caps_per_asset=[
            AssetPositionCapResponse(
                asset="*",
                max_position_size_usd=Decimal(str(settings.max_symbol_exposure)),
            )
        ],
        cost_model_configuration=CostModelConfigurationResponse(
            fixed_commission=Decimal("0"),
            per_share_commission=Decimal("0"),
            default_half_spread=Decimal("0"),
            extra_slippage_bps=Decimal("0"),
            slippage_rate=Decimal("0.0001"),
        ),
        metadata={
            "position_size_caps_source": "MAX_SYMBOL_EXPOSURE",
            "cost_model_source": "simulation defaults",
        },
    )
    return success_response(data=data, request_id=request_id)
