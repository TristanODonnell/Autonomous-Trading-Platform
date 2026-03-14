from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class RunManifestService:
    def __init__(self, session: Session):
        self.session = session

    def save(self, manifest: RunManifest):
        with SorUnitOfWork(self.session) as uow:
            uow.run_manifests.add(manifest)
