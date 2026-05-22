import logging


def get_logger(name: str):
    logger = logging.getLogger(name)
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.propagate = True

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
