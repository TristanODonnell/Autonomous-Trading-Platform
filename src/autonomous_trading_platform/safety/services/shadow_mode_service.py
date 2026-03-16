from autonomous_trading_platform.config.settings import Settings


class ShadowModeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_enabled(self) -> bool:
        return self.settings.shadow_mode_enabled

    def get_suppression_reason(self) -> str | None:
        if self.settings.shadow_mode_enabled:
            return "shadow_mode_enabled"
        return None
