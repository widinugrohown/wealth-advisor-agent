"""Memory layer.

Short-term memory is RunContext (see contracts.py). Long-term memory is one
append-only JSONL line per finished run, so the next run can compare itself with
the past instead of starting blind.
"""
from __future__ import annotations

import json
import os
from typing import Any

from advisor.contracts import RunContext
from advisor.logging_setup import local_now_iso


def recent_insights(client_id: str, n: int = 3, path: str | None = None) -> list[dict[str, Any]]:
    """Last n stored summaries for this client, newest first."""
    path = path or os.getenv("MEMORY_PATH", "memory/insights.jsonl")
    if not os.path.exists(path):
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("client_id") == client_id:
                rows.append(row)
    return rows[-n:][::-1]


def remember(ctx: RunContext, one_line: str, path: str | None = None) -> dict[str, Any]:
    """Append this run's summary. Called once, at the end, even when degraded."""
    path = path or os.getenv("MEMORY_PATH", "memory/insights.jsonl")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    row = {
        "client_id": ctx.client_id,
        "run_id": ctx.run_id,
        "date": local_now_iso(),
        "status": ctx.status,
        "finding_types": sorted({f.type for f in ctx.findings}),
        "max_severity": _max_severity(ctx),
        "n_findings": len(ctx.findings),
        "one_line_summary": one_line,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _max_severity(ctx: RunContext) -> str:
    order = {"low": 1, "medium": 2, "high": 3}
    return max((f.severity for f in ctx.findings), key=lambda s: order[s], default="none")
