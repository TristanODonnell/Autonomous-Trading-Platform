from autonomous_trading_platform.contracts.common.enums import CheckpointStatus
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class DatasetVersionFinalizationService:
    def __init__(self, session):
        self.session = session

    def finalize_backfill_dataset_version(
        self,
        *,
        ingestion_run_id: str,
        dataset_version_id: str,
    ) -> str:
        with SorUnitOfWork(self.session) as uow:
            checkpoints = uow.ingestion_checkpoints.list_for_run_and_dataset(
                ingestion_run_id=ingestion_run_id,
                dataset_version=dataset_version_id,
            )

            dataset_version = uow.dataset_versions.get_by_dataset_version_id(dataset_version_id)
            if dataset_version is None:
                raise ValueError(f"Dataset version {dataset_version_id} not found.")

            all_completed = all(
                checkpoint.checkpoint_status == CheckpointStatus.COMPLETED
                for checkpoint in checkpoints
            )

            if not checkpoints or not all_completed:
                dataset_version.validation_status = "incomplete"

                failed_checkpoints = [
                    checkpoint
                    for checkpoint in checkpoints
                    if checkpoint.checkpoint_status != CheckpointStatus.COMPLETED
                ]
                failure_reasons = [
                    {
                        "symbol": cp.symbol,
                        "date": cp.checkpoint_date.isoformat(),
                        "status": cp.checkpoint_status.value,
                        "error": cp.error_message,
                    }
                    for cp in failed_checkpoints
                ]
                dataset_version.metadata_json = {
                    **(dataset_version.metadata_json or {}),
                    "validation": {
                        "status": dataset_version.validation_status,
                        "failed_checkpoints": failure_reasons,
                    },
                }
            else:
                dataset_version.validation_status = "validated"

                dataset_version.metadata_json = {
                    **(dataset_version.metadata_json or {}),
                    "validation": {
                        "status": dataset_version.validation_status,
                        "failed_checkpoints": [],
                    },
                }

            uow.dataset_versions.upsert(dataset_version)
            return str(dataset_version.validation_status)
