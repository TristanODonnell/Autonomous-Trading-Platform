from __future__ import annotations

from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction

from ..clients import alpaca_corporate_action_client as client
from ..services.corporate_action_normalization_service import parse_alpaca_corporate_action


class IngestCorporateActionsJob:
    def ingest_corporate_actions_job(self) -> list[CorporateAction]:
        payload: dict = client.fetch_corporate_actions()

        raw_actions = payload.get("corporate_actions", [])

        parsed_actions: list[CorporateAction] = [
            parse_alpaca_corporate_action(raw_action) for raw_action in raw_actions
        ]

        return parsed_actions
