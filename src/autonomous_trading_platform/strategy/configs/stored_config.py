"""Reading strategy parameters out of stored strategy_configs.config_json."""

from __future__ import annotations

from typing import Any


def stored_config_parameters(config_json: dict[str, Any] | None) -> dict[str, Any]:
    """The strategy parameters inside a stored config.

    Research stores configs wrapped as {type, parameters, strategy_id}; hand-seeded
    configs are the bare parameter dict.
    """
    config = config_json or {}
    if "parameters" in config and "type" in config:
        return dict(config["parameters"] or {})
    return dict(config)
