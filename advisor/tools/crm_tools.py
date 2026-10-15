"""CRM tools. The only files allowed to import httpx.

Each tool maps one HTTP endpoint to one ToolResult, with timeout, retry,
backoff and field validation. Missing fields do not fail the call: they come
back as ok=True + degraded_reason, because the analyzer can still work.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from advisor import config
from advisor.contracts import ToolResult
from advisor.tools.base import BaseTool

PROFILE_REQUIRED = ("client_id", "name", "risk_profile", "aum_idr")


class _CrmTransport:
    """Thin HTTP layer: timeout, retries, and an error string instead of an exception."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None,
                 retries: int | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or config.CRM_BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else config.CRM_TIMEOUT_S
        self.retries = retries if retries is not None else config.CRM_RETRIES
        # injecting a transport is how the tests exercise failures with no server
        self.transport = transport

    def get_json(self, path: str, params: dict | None = None) -> ToolResult:
        attempts = 0
        last_error = "unknown_error"
        for attempt in range(self.retries + 1):
            attempts = attempt + 1
            try:
                with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                    resp = client.get(f"{self.base_url}{path}", params=params)
                if resp.status_code >= 500:
                    last_error = f"http_{resp.status_code}"
                elif resp.status_code == 404:
                    return ToolResult(ok=False, error="http_404", attempts=attempts)
                else:
                    resp.raise_for_status()
                    return ToolResult(ok=True, data=resp.json(), attempts=attempts)
            except httpx.TimeoutException:
                last_error = "timeout"
            except httpx.HTTPError as exc:
                last_error = f"http_error: {type(exc).__name__}"
            if attempt < self.retries:
                time.sleep(config.CRM_BACKOFF_S[min(attempt, len(config.CRM_BACKOFF_S) - 1)])
        return ToolResult(ok=False, error=last_error, attempts=attempts)


class _CrmTool(BaseTool):
    def __init__(self, transport: _CrmTransport | None = None):
        self.transport = transport or _CrmTransport()


class GetClientProfile(_CrmTool):
    name = "crm.get_profile"
    description = "Fetch one client's profile (risk profile, AUM, holdings, contact)."

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"client_id": {"type": "string"}},
                "required": ["client_id"]}

    def _run(self, client_id: str, **_: Any) -> ToolResult:
        result = self.transport.get_json(f"/clients/{client_id}")
        if not result.ok:
            return result
        missing = [k for k in PROFILE_REQUIRED if result.data.get(k) in (None, "")]
        if missing:
            return ToolResult(ok=True, data=result.data, attempts=result.attempts,
                              error=f"missing_field:{','.join(missing)}",
                              degraded_reason=f"crm_missing_field:{','.join(missing)}")
        return result


class GetTransactions(_CrmTool):
    name = "crm.get_transactions"
    description = "Fetch a client's transaction history, newest first."

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"client_id": {"type": "string"},
                                                 "limit": {"type": "integer"}},
                "required": ["client_id"]}

    def _run(self, client_id: str, limit: int = 200, **_: Any) -> ToolResult:
        result = self.transport.get_json(f"/clients/{client_id}/transactions",
                                         params={"limit": limit})
        if not result.ok:
            return result
        rows = result.data.get("transactions")
        if not isinstance(rows, list):
            return ToolResult(ok=False, error="missing_field:transactions", attempts=result.attempts)
        return result


class GetAdvisorNotes(_CrmTool):
    name = "crm.get_notes"
    description = "Fetch free-text notes left by the human advisor (unstructured context)."

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"client_id": {"type": "string"}},
                "required": ["client_id"]}

    def _run(self, client_id: str, **_: Any) -> ToolResult:
        result = self.transport.get_json(f"/clients/{client_id}/notes")
        if not result.ok:
            # Notes are a nice-to-have: their absence must not degrade the run.
            return ToolResult(ok=False, error=result.error, attempts=result.attempts)
        return result


def build_crm_tools(transport: _CrmTransport | None = None) -> list[BaseTool]:
    shared = transport or _CrmTransport()
    return [GetClientProfile(shared), GetTransactions(shared), GetAdvisorNotes(shared)]
