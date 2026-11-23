"""The pipeline's log lines reach a job's log.

Found 2026-11-22: a worker job's log held no research line. A logger taken from
``logging.getLogger`` has no handler in a job's process and inherits the root's level,
so its INFO lines were dropped before any handler saw them. The pipeline's modules take
theirs from ``observability.logging.get_logger``, which gives the logger a handler and
the INFO level. A module added later with a plain logger would go quiet the same way,
with nothing to show for it; this test is what shows it.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh
import autonomous_trading_platform.research.pipeline as pipeline


def _modules_with_a_logger() -> dict[str, logging.Logger]:
    found: dict[str, logging.Logger] = {rh.__name__: rh.logger}
    for info in pkgutil.walk_packages(pipeline.__path__, prefix=f"{pipeline.__name__}."):
        module = importlib.import_module(info.name)
        logger = getattr(module, "logger", None)
        if isinstance(logger, logging.Logger):
            found[info.name] = logger
    return found


def test_every_pipeline_module_that_logs_can_be_read_in_a_job_s_log() -> None:
    loggers = _modules_with_a_logger()

    # The stages, the runner, the two reports and the hook: the list is not empty by luck.
    assert len(loggers) >= 10, sorted(loggers)
    quiet = sorted(
        name
        for name, logger in loggers.items()
        if not logger.handlers or logger.level != logging.INFO or logger.disabled
    )
    assert quiet == [], f"take the logger from observability.logging.get_logger: {quiet}"
