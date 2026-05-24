from enum import StrEnum


class CostModelType(StrEnum):
    FIXED_BPS = "fixed_bps"
    VOLUME_SHARE = "volume_share"
    SPREAD_AWARE = "spread_aware"
