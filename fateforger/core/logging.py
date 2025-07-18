import logging


def get_logger(name: str) -> logging.Logger:
    """Return application logger."""
    logger = logging.getLogger(f"fateforger.{name}")
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO)
    return logger
