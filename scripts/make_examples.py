"""Produce the sample input/output files in examples/.

Three real runs of the same client, so a reviewer can see the behaviour without
running anything:

  output_client_001.json             first run
  output_client_001_memory_run2.json second run - the reporter compares with the stored first run
  output_client_001_overridden.json  same run with the human checkpoint answered "override"

The mock CRM is started and stopped by this script, and the checkpoint answer is
scripted, so the whole thing is one command:

    python scripts/make_examples.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from advisor.orchestrator import Orchestrator                                    # noqa: E402
from advisor.tools.analysis_tools import build_analysis_tools                     # noqa: E402
from advisor.tools.base import ToolRegistry                                       # noqa: E402
from advisor.tools.crm_tools import _CrmTransport, build_crm_tools                # noqa: E402

PORT = int(os.getenv("CRM_PORT", "8079"))
CLIENT = os.getenv("EXAMPLE_CLIENT", "c001")


def wait_for_crm(base_url: str, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.3)
    return False


def run(client_id: str, base_url: str, *, interactive: bool, scripted: list[str] | None = None,
        memory_path: str) -> dict:
    if scripted:
        answers = iter(scripted)
        import builtins
        builtins.input = lambda *_: next(answers)  # type: ignore[assignment]
    # the transport is given the base url explicitly: advisor.config was imported
    # before this script chose its port
    tools = ToolRegistry(build_crm_tools(_CrmTransport(base_url=base_url))
                         + build_analysis_tools())
    orchestrator = Orchestrator(tools, runs_dir="runs-examples", use_llm=False,
                                auto_approve=not interactive, interactive=interactive,
                                memory_path=memory_path)
    return orchestrator.run_client(client_id).to_dict()


def main() -> int:
    base_url = f"http://127.0.0.1:{PORT}"
    os.environ["CRM_BASE_URL"] = base_url
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_crm.server:app",
                               "--port", str(PORT), "--log-level", "error"], cwd=REPO)
    examples = REPO / "examples"
    examples.mkdir(exist_ok=True)
    try:
        if not wait_for_crm(base_url):
            print("mock CRM did not start")
            return 1
        memory_path = str(examples / "memory_sample.jsonl")
        if os.path.exists(memory_path):
            os.remove(memory_path)

        first = run(CLIENT, base_url, interactive=False, memory_path=memory_path)
        (examples / "output_client_001.json").write_text(
            json.dumps(first, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        second = run(CLIENT, base_url, interactive=False, memory_path=memory_path)
        (examples / "output_client_001_memory_run2.json").write_text(
            json.dumps(second, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        third = run(CLIENT, base_url, interactive=True, scripted=["o", "call the client before acting"],
                    memory_path=memory_path)
        (examples / "output_client_001_overridden.json").write_text(
            json.dumps(third, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        shutil.copy(REPO / "data" / "clients" / f"{CLIENT}.json",
                    examples / "input_client_001.json")

        print(f"{CLIENT}: status run1={first['status']} run2={second['status']} "
              f"findings run1={len(first['findings'])} run2={len(second['findings'])} "
              f"hitl={third['human_review']['decision']}")
        print(f"memory used in run2: {second['metrics']['memory_insights_used']} stored insight(s)")
        print(f"run2 summary line 2: {second['summary_text'].splitlines()[1][:120]}")
        print(f"override note: {third['recommendations'][0]['rationale'][:80]}")
        print(f"written to {examples}")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
