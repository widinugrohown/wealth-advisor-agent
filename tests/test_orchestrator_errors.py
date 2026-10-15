"""The error-handling and human-checkpoint claims, as tests.

Every test here runs the real orchestrator with a faked network layer, so the
failure travels the same path a real outage would.
"""
import json

import httpx

from advisor.contracts import RunContext
from advisor.orchestrator import Orchestrator
from advisor.tools.analysis_tools import build_analysis_tools
from advisor.tools.base import ToolRegistry
from advisor.tools.crm_tools import _CrmTransport, build_crm_tools

AUM = 1_000_000_000
PROFILE = {"client_id": "c001", "name": "Client 001", "risk_profile": "moderate", "aum_idr": AUM}
NORMAL_TX = [{"tx_id": f"n{i:03d}", "date": f"2026-0{1 + i % 9}-{1 + i % 27:02d}",
              "amount_idr": -(3_000_000 + i * 10_000), "category": "groceries",
              "counterparty": f"cp{i % 5:03d}", "channel": "mobile", "note": ""}
             for i in range(30)]
SPIKE_TX = {"tx_id": "t900", "date": "2026-09-30", "amount_idr": -800_000_000,
            "category": "transfer_out", "counterparty": "cp_big_001", "channel": "mobile",
            "note": ""}


def registry_with(handler, retries: int = 1) -> ToolRegistry:
    transport = _CrmTransport(base_url="http://fake", timeout=0.05, retries=retries,
                              transport=httpx.MockTransport(handler))
    return ToolRegistry(build_crm_tools(transport) + build_analysis_tools())


def always(*_args, **_kwargs):
    raise httpx.ConnectTimeout("mock outage")


def missing_client(request):
    return httpx.Response(404, json={"detail": "unknown client"})


def healthy(transactions):
    def handler(request):
        if request.url.path.endswith("/transactions"):
            return httpx.Response(200, json={"client_id": "c001", "transactions": transactions})
        if request.url.path.endswith("/notes"):
            return httpx.Response(200, json={"client_id": "c001", "notes": []})
        return httpx.Response(200, json=PROFILE)

    return handler


def orchestrator(tmp_path, registry, **kwargs) -> Orchestrator:
    defaults = {"runs_dir": str(tmp_path / "runs"), "use_llm": False,
                "auto_approve": True, "interactive": False}
    defaults.update(kwargs)
    return Orchestrator(registry, **defaults)


def events_of(output, tmp_path) -> list[dict]:
    path = tmp_path / "runs" / output.run_id / "events.jsonl"
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_run_completes_when_the_crm_always_fails(tmp_path):
    output = orchestrator(tmp_path, registry_with(always, retries=1)).run_client("c001")
    assert output.status == "degraded", "an outage degrades the run, it does not end it"
    assert any(reason.startswith("crm_failed:client_profile") for reason in output.degraded_reasons)
    events = events_of(output, tmp_path)
    assert any(e["event"] == "run_end" and e["status"] == "degraded" for e in events)
    assert any(e["event"] == "tool_result" and e["status"] == "error" for e in events)
    assert sum(1 for e in events if e["event"] == "tool_call") >= 3


def test_missing_client_is_a_failed_run_not_a_degraded_one(tmp_path):
    output = orchestrator(tmp_path, registry_with(missing_client)).run_client("nope")
    assert output.status == "failed"
    assert any("http_404" in reason for reason in output.degraded_reasons)
    assert output.findings == []


def test_no_transactions_degrades_instead_of_crashing(tmp_path):
    output = orchestrator(tmp_path, registry_with(healthy([]))).run_client("c001")
    assert output.status == "degraded"
    assert "no_transactions" in output.degraded_reasons


def test_high_severity_finding_opens_the_human_checkpoint(tmp_path, monkeypatch):
    answers = iter(["o", "call the client before acting"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    output = orchestrator(tmp_path, registry_with(healthy(NORMAL_TX + [SPIKE_TX])),
                          auto_approve=False, interactive=True).run_client("c001")
    assert output.human_review.required is True
    assert output.human_review.decision == "overridden"
    assert output.recommendations[0].action == "human_override"
    assert "call the client" in output.recommendations[0].rationale
    decisions = [e for e in events_of(output, tmp_path) if e["event"] == "hitl_decision"]
    assert decisions and decisions[0]["detail"]["decision"] == "overridden"


def test_reject_clears_the_recommendations(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "r")
    output = orchestrator(tmp_path, registry_with(healthy(NORMAL_TX + [SPIKE_TX])),
                          auto_approve=False, interactive=True).run_client("c001")
    assert output.human_review.decision == "rejected"
    assert output.recommendations == []


def test_low_severity_run_does_not_stop(tmp_path):
    output = orchestrator(tmp_path, registry_with(healthy(NORMAL_TX)),
                          auto_approve=False, interactive=True).run_client("c001")
    assert output.human_review.required is False
    assert output.human_review.decision == "skipped"


def test_memory_and_output_files_are_written(tmp_path):
    memory_path = str(tmp_path / "memory" / "insights.jsonl")
    orchestrator(tmp_path, registry_with(healthy(NORMAL_TX)),
                 memory_path=memory_path).run_client("c001")
    orchestrator(tmp_path, registry_with(healthy(NORMAL_TX)),
                 memory_path=memory_path).run_client("c001")
    with open(memory_path, encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    assert len(lines) == 2, "one memory line per run"
    assert lines[0]["client_id"] == "c001"
