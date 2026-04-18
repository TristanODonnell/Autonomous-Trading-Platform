from __future__ import annotations

from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.contracts.validators.core import ValidationResult, run_rules
from autonomous_trading_platform.contracts.validators.feature_dataset_version import (
    FEATURE_DATASET_VERSION_RULES,
)


class FeatureDatasetValidationService:
    def validate_feature_dataset(
        self, feature_dataset_version: FeatureDatasetVersion
    ) -> ValidationResult:
        return run_rules(feature_dataset_version, FEATURE_DATASET_VERSION_RULES)
