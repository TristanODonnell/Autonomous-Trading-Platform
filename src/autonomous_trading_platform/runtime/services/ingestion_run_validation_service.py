from __future__ import annotations

from autonomous_trading_platform.contracts.runtime.ingestion_run import IngestionRun
from autonomous_trading_platform.contracts.validators.core import ValidationResult, run_rules
from autonomous_trading_platform.contracts.validators.ingestion_run import INGESTION_RUN_RULES


class IngestionRunValidationService:
    def validate_ingestion_run(self, ingestion_run: IngestionRun) -> ValidationResult:
        return run_rules(ingestion_run, INGESTION_RUN_RULES)
