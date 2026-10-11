import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Exception details are deliberately omitted: libraries may include user input.
        return json.dumps(
            {
                "level": record.levelname,
                "event": record.getMessage(),
                **getattr(record, "safe_fields", {}),
            },
            allow_nan=False,
        )


def configure_logging(level: str = "INFO"):
    logger = logging.getLogger("edge_router")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False


def log_event(event: str, **safe_fields):
    logging.getLogger("edge_router").info(event, extra={"safe_fields": safe_fields})
