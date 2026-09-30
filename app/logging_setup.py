"""Structured JSON logging with a per-request id. Never pass secrets/headers/bodies to these loggers."""
import contextvars
import json
import logging
import sys
from datetime import datetime, UTC

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage(), "requestId": request_id_var.get()}
        out.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)   # server logs only; never sent to clients
        return json.dumps(out, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single lines for local development:  12:03:41 WARNING app: message  key=value  [rid abc123]"""
    COLORS = {"WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[31;1m", "INFO": "\033[36m", "DEBUG": "\033[90m"}

    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {}) or {}
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        rid = request_id_var.get()
        lvl = record.levelname
        if self.color:
            lvl = f"{self.COLORS.get(lvl, '')}{lvl:<7}\033[0m"
        else:
            lvl = f"{lvl:<7}"
        line = f"{datetime.now().strftime('%H:%M:%S')} {lvl} {record.name.removeprefix('buildguard.')}: {record.getMessage()}"
        if extra:
            line += f"  {extra}"
        if rid != "-":
            line += f"  [rid {rid}]"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO", fmt: str = "auto", env: str = "dev") -> None:
    h = logging.StreamHandler(sys.stdout)
    use_console = fmt == "console" or (fmt == "auto" and env in ("dev", "test"))
    h.setFormatter(ConsoleFormatter(color=sys.stdout.isatty()) if use_console else JsonFormatter())
    root = logging.getLogger("buildguard")
    root.handlers[:] = [h]
    root.setLevel(level.upper())
    root.propagate = False
