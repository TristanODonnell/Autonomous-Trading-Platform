# src/autonomous_trading_platform/runtime/services/daily_dataset_version_resolver_service.py

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions


class DailyDatasetVersionResolverService:
    def __init__(
        self,
        session: Session,
        dataset_registration_service: DatasetRegistrationService | None = None,
    ) -> None:
        self.session = session
        self.dataset_registration_service = (
            dataset_registration_service or DatasetRegistrationService(session=session)
        )

    def get_or_create_active_daily_dataset(
        self,
        *,
        dataset_name: str,
        price_basis: PriceBasis,
        interval: BarInterval,
        trading_date: date,
        created_at: datetime,
        source: str,
        schema_version: str,
        symbol_coverage: int | None,
        date_coverage_start: date,
        date_coverage_end: date,
        source_manifest: dict[str, Any],
        metadata_json: dict[str, Any] | None = None,
    ) -> DatasetVersion:
        existing_row = (
            self.session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == dataset_name)
            .filter(DatasetVersions.price_basis == price_basis.value)
            .filter(DatasetVersions.interval == interval.value)
            .filter(DatasetVersions.date_coverage_start == trading_date)
            .filter(DatasetVersions.date_coverage_end == trading_date)
            .filter(
                DatasetVersions.metadata_json["dataset_lifecycle"].as_string()
                == "active_daily_incremental"
            )
            .order_by(DatasetVersions.created_at.desc())
            .first()
        )

        if existing_row is not None:
            existing = self.dataset_registration_service.get_by_dataset_version_id(
                existing_row.dataset_version_id
            )
            if existing is None:
                raise RuntimeError(
                    f"Dataset version row exists but contract lookup failed: "
                    f"{existing_row.dataset_version_id}"
                )
            return existing

        dataset_version_id = generate_dataset_version(dataset_name)

        dataset_version = DatasetVersion(
            dataset_version_id=dataset_version_id,
            dataset_name=dataset_name,
            created_at=created_at,
            source=source,
            price_basis=price_basis,
            interval=interval,
            schema_version=schema_version,
            symbol_coverage=symbol_coverage,
            date_coverage_start=date_coverage_start,
            date_coverage_end=date_coverage_end,
            validation_status="unvalidated",
            checksum=None,
            source_manifest=source_manifest,
            metadata_json={
                **(metadata_json or {}),
                "dataset_lifecycle": "active_daily_incremental",
                "trading_date": trading_date.isoformat(),
            },
        )

        return self.dataset_registration_service.register(dataset_version)
