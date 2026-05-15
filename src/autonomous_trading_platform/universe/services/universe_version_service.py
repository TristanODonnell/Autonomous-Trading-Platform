# autonomous_trading_platform/universe/services/universe_version_service.py

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)


class UniverseVersionService:
    def __init__(self, repository: UniverseVersionRepository) -> None:
        self.repository = repository

    def build_version(
        self,
        *,
        name: str,
        effective_from: datetime,
        symbols: list[str],
        source: str = UniverseSource.CUSTOM,
        rebalance_reason: str | None = None,
    ) -> tuple[UniverseVersion, list[UniverseMember]]:
        normalized_symbols = self._normalize_symbols(symbols)
        config_hash = self._compute_config_hash(normalized_symbols)
        version_id = str(uuid.uuid4())
        now_utc = datetime.now(UTC)

        version = UniverseVersion(
            universe_version_id=version_id,
            name=name,
            source=source,
            created_at=now_utc,
            effective_from=self._ensure_utc(effective_from),
            effective_to=None,
            status=UniverseStatus.CANDIDATE,
            rebalance_reason=rebalance_reason,
            config_hash=config_hash,
        )

        members = [
            UniverseMember(
                universe_version_id=version_id,
                symbol=symbol,
                rank=rank,
                score=None,
                included_reason=source,
                excluded_reason=None,
                liquidity_metrics_json=None,
                quality_metrics_json=None,
            )
            for rank, symbol in enumerate(normalized_symbols)
        ]

        return version, members

    def save_version(
        self,
        version: UniverseVersion,
        members: list[UniverseMember],
    ) -> tuple[UniverseVersion, list[UniverseMember]]:
        self.repository.insert_version(version)
        self.repository.insert_members(members)
        return version, members

    def build_and_activate_version(
        self,
        *,
        name: str,
        effective_from: datetime,
        symbols: list[str],
        source: str = UniverseSource.CUSTOM,
        rebalance_reason: str | None = None,
    ) -> tuple[UniverseVersion, list[UniverseMember]]:
        version, members = self.build_version(
            name=name,
            effective_from=effective_from,
            symbols=symbols,
            source=source,
            rebalance_reason=rebalance_reason,
        )
        self.repository.retire_active_version(version.effective_from)
        self.repository.insert_version(version)
        self.repository.insert_members(members)
        self.repository.activate_version(version.universe_version_id)
        return version, members

    def _compute_config_hash(self, symbols: list[str]) -> str:
        normalized = self._normalize_symbols(symbols)
        payload = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_symbols(symbols: list[str]) -> list[str]:
        normalized = {
            symbol.strip().upper() for symbol in symbols if symbol is not None and symbol.strip()
        }
        return sorted(normalized)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
