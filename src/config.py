# src/config.py

import os


class Settings:
    """
    Minimal environment configuration loader.
    Fails fast if required variables are missing.
    """

    def __init__(self) -> None:
        self.app_env = self._get_required("APP_ENV")
        self.database_url = self._get_required("DATABASE_URL")

    def _get_required(self, key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise RuntimeError(f"Missing required environment variable: {key}")
        return value
