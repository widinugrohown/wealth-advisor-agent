"""Tools never raise: a faked transport, three failure modes, three assertions.

The point of the tool layer is that a failure is data. These tests prove it
without a running server, which is also why the analyzer can be tested alone.
"""
import httpx
import pytest

from advisor.contracts import ToolResult
from advisor.tools.base import BaseTool, ToolRegistry
from advisor.tools.crm_tools import GetClientProfile, GetTransactions, _CrmTransport


class FakeTool(BaseTool):
    name = "fake.explode"
    description = "raises on purpose"

    def _run(self, **kwargs) -> ToolResult:
        raise ValueError("boom")


def transport_with(handler, retries: int = 1) -> _CrmTransport:
    """A real transport with a faked network layer: no server needed."""
    return _CrmTransport(base_url="http://fake", timeout=0.05, retries=retries,
                         transport=httpx.MockTransport(handler))


def test_tool_exception_becomes_result_not_traceback():
    result = FakeTool().run()
    assert result.ok is False
    assert "ValueError" in result.error
    assert result.latency_ms >= 0
    assert result.tool == "fake.explode"


def test_registry_runs_a_registered_tool():
    registry = ToolRegistry([FakeTool()])
    assert registry.names() == ["fake.explode"]
    assert registry.run("fake.explode").ok is False


def test_unknown_tool_is_a_keyerror():
    with pytest.raises(KeyError):
        ToolRegistry([]).get("nope")


def test_registry_specs_are_openai_shaped():
    spec = ToolRegistry([FakeTool()]).specs()[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "fake.explode"


def test_timeout_is_retried_and_reported():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("mock timeout")

    result = GetClientProfile(transport_with(handler, retries=2)).run(client_id="c001")
    assert result.ok is False
    assert result.error == "timeout"
    assert result.attempts == 3, "one initial attempt plus two retries"
    assert calls["n"] == 3


def test_http_500_is_retried_then_reported():
    def handler(request):
        return httpx.Response(500, json={"detail": "mock internal error"})

    result = GetClientProfile(transport_with(handler, retries=1)).run(client_id="c001")
    assert result.ok is False
    assert result.error == "http_500"
    assert result.attempts == 2


def test_404_stops_immediately():
    def handler(request):
        return httpx.Response(404, json={"detail": "unknown client"})

    result = GetClientProfile(transport_with(handler, retries=3)).run(client_id="nope")
    assert result.ok is False
    assert result.error == "http_404"
    assert result.attempts == 1, "a missing client is not worth retrying"


def test_missing_required_field_degrades_instead_of_failing():
    def handler(request):
        return httpx.Response(200, json={"client_id": "c001", "name": "A"})

    result = GetClientProfile(transport_with(handler)).run(client_id="c001")
    assert result.ok is True
    assert result.error == "missing_field:risk_profile,aum_idr"
    assert result.degraded_reason == "crm_missing_field:risk_profile,aum_idr"


def test_transactions_payload_must_be_a_list():
    def handler(request):
        return httpx.Response(200, json={"client_id": "c001", "transactions": "oops"})

    result = GetTransactions(transport_with(handler)).run(client_id="c001")
    assert result.ok is False
    assert result.error == "missing_field:transactions"
