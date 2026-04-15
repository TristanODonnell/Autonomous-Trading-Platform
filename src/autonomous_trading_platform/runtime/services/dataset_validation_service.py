from __future__ import annotations

from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.contracts.validators.core import ValidationResult, run_rules
from autonomous_trading_platform.contracts.validators.dataset_version import DATASET_VERSION_RULES


class DatasetValidationService:
    def validate_dataset(self, dataset_version: DatasetVersion) -> ValidationResult:
        return run_rules(dataset_version, DATASET_VERSION_RULES)
