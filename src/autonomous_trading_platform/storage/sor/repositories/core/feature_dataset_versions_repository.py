from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class FeatureDatasetVersionsRepository(BaseRepository):
    def get_by_dataset_version_id(
        self,
        dataset_version_id: str,
    ) -> FeatureDatasetVersions | None:
        stmt = select(FeatureDatasetVersions).where(
            FeatureDatasetVersions.dataset_version_id == dataset_version_id
        )
        return cast(
            FeatureDatasetVersions | None,
            self.session.scalars(stmt).one_or_none(),
        )

    def insert(self, row: FeatureDatasetVersions) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[FeatureDatasetVersions]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: FeatureDatasetVersions) -> FeatureDatasetVersions:
        existing = self.get_by_dataset_version_id(row.dataset_version_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in FeatureDatasetVersions.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_dataset_version_id(self, dataset_version_id: str) -> None:
        obj = self.get_by_dataset_version_id(dataset_version_id)
        if obj is not None:
            self.session.delete(obj)

    def to_row(self, contract: FeatureDatasetVersion) -> FeatureDatasetVersions:
        return FeatureDatasetVersions(
            dataset_version_id=contract.dataset_version_id,
            feature_name=contract.feature_name,
            dataset_name=contract.dataset_name,
            created_at=contract.created_at,
            schema_version=contract.schema_version,
            source_dataset_version=contract.source_dataset_version,
            underlying_price_basis=contract.underlying_price_basis,
            computation_parameters=contract.computation_parameters,
            computation_code_version=contract.computation_code_version,
            storage_path=contract.storage_path,
            symbol_coverage=contract.symbol_coverage,
            date_coverage_start=contract.date_coverage_start,
            date_coverage_end=contract.date_coverage_end,
            validation_status=contract.validation_status,
            checksum=contract.checksum,
            source_manifest=contract.source_manifest,
            metadata_json=contract.metadata_json,
        )

    def to_contract(self, row: FeatureDatasetVersions) -> FeatureDatasetVersion:
        return FeatureDatasetVersion(
            dataset_version_id=row.dataset_version_id,
            feature_name=row.feature_name,
            dataset_name=row.dataset_name,
            created_at=row.created_at,
            schema_version=row.schema_version,
            source_dataset_version=cast(str, row.source_dataset_version),
            underlying_price_basis=row.underlying_price_basis,
            computation_parameters=row.computation_parameters,
            computation_code_version=row.computation_code_version,
            storage_path=row.storage_path,
            symbol_coverage=row.symbol_coverage,
            date_coverage_start=row.date_coverage_start,
            date_coverage_end=row.date_coverage_end,
            validation_status=row.validation_status,
            checksum=row.checksum,
            source_manifest=row.source_manifest,
            metadata_json=row.metadata_json,
        )

    def get_latest_validated(
        self,
        *,
        feature_name: str,
        underlying_price_basis: PriceBasis,
    ) -> FeatureDatasetVersions | None:
        stmt = (
            select(FeatureDatasetVersions)
            .where(
                FeatureDatasetVersions.feature_name == feature_name,
                FeatureDatasetVersions.underlying_price_basis == underlying_price_basis,
                FeatureDatasetVersions.validation_status == "validated",
            )
            .order_by(FeatureDatasetVersions.created_at.desc())
        )
        return cast(
            FeatureDatasetVersions | None,
            self.session.scalars(stmt).first(),
        )

    def list_validated_by_feature_name(
        self,
        *,
        feature_name: str,
    ) -> list[FeatureDatasetVersions]:
        stmt = (
            select(FeatureDatasetVersions)
            .where(
                FeatureDatasetVersions.feature_name == feature_name,
                FeatureDatasetVersions.validation_status == "validated",
            )
            .order_by(FeatureDatasetVersions.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def list_validated_by_coverage_and_price_basis(
        self,
        *,
        feature_name: str,
        underlying_price_basis: PriceBasis,
        start_date,
        end_date,
    ) -> list[FeatureDatasetVersions]:
        stmt = (
            select(FeatureDatasetVersions)
            .where(
                FeatureDatasetVersions.feature_name == feature_name,
                FeatureDatasetVersions.underlying_price_basis == underlying_price_basis,
                FeatureDatasetVersions.validation_status == "validated",
                FeatureDatasetVersions.date_coverage_start <= start_date,
                FeatureDatasetVersions.date_coverage_end >= end_date,
            )
            .order_by(FeatureDatasetVersions.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def get_source_dataset_version_by_feature_dataset_version_id(
        self,
        *,
        feature_dataset_version_id: str,
    ) -> str | None:
        row = self.get_by_dataset_version_id(feature_dataset_version_id)
        if row is None:
            return None
        return cast(str, row.source_dataset_version)

    def list_by_source_dataset_version(
        self,
        *,
        source_dataset_version: str,
    ) -> list[FeatureDatasetVersions]:
        stmt = (
            select(FeatureDatasetVersions)
            .where(FeatureDatasetVersions.source_dataset_version == source_dataset_version)
            .order_by(FeatureDatasetVersions.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def list_validated_by_source_dataset_version(
        self,
        *,
        source_dataset_version: str,
    ) -> list[FeatureDatasetVersions]:
        stmt = (
            select(FeatureDatasetVersions)
            .where(
                FeatureDatasetVersions.source_dataset_version == source_dataset_version,
                FeatureDatasetVersions.validation_status == "validated",
            )
            .order_by(FeatureDatasetVersions.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def find_matching_dataset(
        self,
        *,
        feature_name: str,
        source_dataset_version_id: str,
        symbols: list[str] | None,
        start_date,
        end_date,
        computation_parameters: dict[str, object],
    ) -> FeatureDatasetVersion | None:
        stmt = (
            select(FeatureDatasetVersions)
            .where(
                FeatureDatasetVersions.feature_name == feature_name,
                FeatureDatasetVersions.source_dataset_version == source_dataset_version_id,
                FeatureDatasetVersions.computation_parameters == computation_parameters,
                FeatureDatasetVersions.validation_status == "validated",
            )
            .order_by(FeatureDatasetVersions.created_at.desc())
        )

        rows = cast(
            list[FeatureDatasetVersions],
            list(self.session.scalars(stmt).all()),
        )

        for row in rows:
            if start_date is not None and row.date_coverage_start > start_date:
                continue

            if end_date is not None and row.date_coverage_end < end_date:
                continue

            if symbols:
                requested_symbol_count = len({symbol.upper() for symbol in symbols})
                existing_symbol_count = row.symbol_coverage or 0

                if existing_symbol_count < requested_symbol_count:
                    continue

            return self.to_contract(row)

        return None

    def find_for_simulation(
        self,
        *,
        feature_name: str,
        source_dataset_version: str,
        price_basis: PriceBasis,
        start_date,
        end_date,
        min_symbol_count: int,
    ) -> FeatureDatasetVersions | None:
        """Return the most-recent validated feature dataset that satisfies all
        simulation lineage requirements: source version, price basis, date
        coverage, and minimum symbol count.

        Intentionally omits computation_parameters so strategies that declare
        a feature by name (not by specific computation parameters) can resolve
        any validated variant of that feature.
        """
        stmt = (
            select(FeatureDatasetVersions)
            .where(
                FeatureDatasetVersions.feature_name == feature_name,
                FeatureDatasetVersions.source_dataset_version == source_dataset_version,
                FeatureDatasetVersions.underlying_price_basis == price_basis,
                FeatureDatasetVersions.validation_status == "validated",
                FeatureDatasetVersions.date_coverage_start <= start_date,
                FeatureDatasetVersions.date_coverage_end >= end_date,
            )
            .order_by(FeatureDatasetVersions.created_at.desc())
        )

        rows = cast(
            list[FeatureDatasetVersions],
            list(self.session.scalars(stmt).all()),
        )

        for row in rows:
            if (row.symbol_coverage or 0) >= min_symbol_count:
                return row

        return None

    def save(self, contract: FeatureDatasetVersion) -> FeatureDatasetVersion:
        row = self.to_row(contract)
        saved = self.upsert(row)
        self.session.flush()

        return self.to_contract(saved)
