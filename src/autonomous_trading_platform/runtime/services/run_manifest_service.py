from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class RunManifestService:
    def __init__(self, session: Session):
        self.session = session

    def save(self, manifest: RunManifest) -> RunManifest:
        with SorUnitOfWork(self.session) as uow:
            existing = uow.run_manifests.get_by_run_id(manifest.run_id)
            if existing is not None:
                return uow.run_manifests.to_contract(existing)

            row = uow.run_manifests.add(manifest)
            return uow.run_manifests.to_contract(row)
