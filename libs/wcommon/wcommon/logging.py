import json
import logging
import sys
from datetime import datetime, timezone

from wcommon.correlation import correlation_id_var

_SERVICE = "unknown"
_logger = logging.getLogger("wcommon")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "service": _SERVICE,
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "correlation_id": correlation_id_var.get() or None,
        }
        data.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)


def configure_logging(service: str) -> None:
    global _SERVICE
    _SERVICE = service
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("botocore", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("pika").setLevel(logging.CRITICAL)


def log(event: str, level: str = "INFO", **fields) -> None:
    _logger.log(getattr(logging, level.upper(), logging.INFO), event,
                extra={"event": event, "fields": fields})
