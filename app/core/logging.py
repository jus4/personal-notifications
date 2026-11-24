import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

NOISY_LOGGERS = ("httpx", "httpcore")


def resolve_log_level(value: str) -> int:
    level = logging.getLevelNamesMapping().get(value.strip().upper())
    if level is None:
        raise ValueError(f"unknown log level: {value!r}")
    return level


def configure_logging(level: str = "INFO") -> None:
    numeric_level = resolve_log_level(level)
    root = logging.getLogger()

    if root.handlers:
        root.setLevel(numeric_level)
    else:
        logging.basicConfig(
            level=numeric_level,
            format=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT,
        )

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
