"""Structured JSONL logging: one line per event, machine-readable.

The library never prints. Humans read the CLI summary in main.py; reviewers read
runs/<run_id>/events.jsonl and scripts/latency_report.py.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
              "warn": logging.WARNING, "error": logging.ERROR}


def local_now_iso() -> str:
    """Timezone-aware local timestamp, millisecond precision."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload = dict(getattr(record, "payload", {}))
        payload.setdefault("ts", local_now_iso())
        payload.setdefault("level", record.levelname.lower())
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(run_id: str = "-", runs_dir: str = "runs", console: bool = False) -> logging.Logger:
    """Logger writing JSONL to runs/<run_id>/events.jsonl plus an optional console line."""
    logger = logging.getLogger(f"advisor.{run_id}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if logger.handlers:
        return logger

    os.makedirs(os.path.join(runs_dir, run_id), exist_ok=True)
    fh = logging.FileHandler(os.path.join(runs_dir, run_id, "events.jsonl"), encoding="utf-8")
    fh.setFormatter(JsonlFormatter())
    logger.addHandler(fh)

    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(ch)
    return logger


def log_event(logger: logging.Logger, event: str, agent: str, *, level: str = "info",
              status: str = "ok", tool: str | None = None, latency_ms: int | None = None,
              attempt: int | None = None, **detail) -> dict:
    """Emit one event line. Every tool call has a matching tool_result line."""
    payload = {"run_id": getattr(logger, "run_id", None), "event": event,
               "agent": agent, "status": status}
    if tool is not None:
        payload["tool"] = tool
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms
    if attempt is not None:
        payload["attempt"] = attempt
    payload["detail"] = detail
    logger.log(LOG_LEVELS.get(level, logging.INFO), event, extra={"payload": payload})
    return payload
