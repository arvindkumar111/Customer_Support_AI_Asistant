"""Structured JSON logging with no raw customer identifiers (Step 6b).

One JSON line per event on stdout (collected by the platform). Rules:
  - customer ids are HMAC-hashed with a secret, so logs can be joined per customer but not reversed without the secret
  - message text is the already-REDACTED text, cut to 200 chars, and only if config.LOG_MESSAGE_TEXT
  - reply text is never logged
"""
import hashlib
import hmac
import json
import logging
import sys
from datetime import datetime, timezone

from app import config


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name}
        event = getattr(record, "event", None)
        if event:
            out.update(event)
        else:
            out["msg"] = record.getMessage()
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return                                            # idempotent
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(level)


def hash_customer(customer_id: str | None) -> str | None:
    if not customer_id:
        return None
    return hmac.new(config.LOG_HASH_SECRET.encode(), customer_id.encode(), hashlib.sha256).hexdigest()[:12]


def log_interaction(**fields) -> None:
    logging.getLogger("interaction").info("interaction", extra={"event": {"event": "interaction", **fields}})