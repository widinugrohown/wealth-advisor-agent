"""Run the pipeline against a mock CRM that is failing on purpose.

The claim "errors are handled" is only worth something if it is measured. This
script starts the mock CRM at three failure rates, runs N clients at each level
and counts: completed runs, degraded runs, failures, crashes.

    python scripts/run_chaos_matrix.py --levels 0,0.3,0.8 --runs-per-level 10

Each level gets its own CRM process (the failure rate is read from the server's
environment), its own output directory, and the same synthetic clients.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor.orchestrator import Orchestrator           # noqa: E402
from advisor.tools.analysis_tools import build_analysis_tools  # noqa: E402
from advisor.tools.base import ToolRegistry             # noqa: E402
from advisor.tools.crm_tools import _CrmTransport, build_crm_tools  # noqa: E402

PORT = int(os.getenv("CHAOS_PORT", "8078"))


def wait_for_crm(base_url: str, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.3)
    return False


def run_level(chaos: float, n_runs: int, client_id: str, runs_dir: str) -> dict:
    """One level = one mock CRM process with its own failure rate.

    Test transport settings are deliberately short (documented in the summary):
    a real outage test should not take an hour. The client is given the base url
    explicitly - advisor.config was imported before this port was chosen.
    """
    base_url = f"http://127.0.0.1:{PORT}"
    server_env = os.environ.copy()
    server_env.update({"CRM_CHAOS": str(chaos), "CRM_CHAOS_SEED": "7",
                       "CRM_FAKE_DELAY_S": os.getenv("CRM_FAKE_DELAY_S", "1.5")})
    client_timeout = float(os.getenv("CRM_MATRIX_TIMEOUT_S", "1.0"))
    client_retries = int(os.getenv("CRM_MATRIX_RETRIES", "1"))

    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_crm.server:app",
                               "--port", str(PORT), "--log-level", "error"], env=server_env)
    counters: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    try:
        if not wait_for_crm(base_url):
            return {"chaos": chaos, "error": "mock CRM did not start"}
        for _ in range(n_runs):
            transport = _CrmTransport(base_url=base_url, timeout=client_timeout,
                                      retries=client_retries)
            orchestrator = Orchestrator(ToolRegistry(build_crm_tools(transport)
                                                     + build_analysis_tools()),
                                        runs_dir=runs_dir, use_llm=False,
                                        auto_approve=True, interactive=False)
            try:
                output = orchestrator.run_client(client_id)
            except Exception as exc:  # noqa: BLE001 - a crash is exactly what we count
                counters["crashed"] += 1
                reasons[f"crash:{type(exc).__name__}"] += 1
                continue
            counters[output.status] += 1
            for reason in output.degraded_reasons:
                reasons[reason] += 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    return {"chaos": chaos, "runs": n_runs, "ok": counters["ok"],
            "degraded": counters["degraded"], "failed": counters["failed"],
            "crashed": counters["crashed"], "reasons": dict(reasons.most_common(8)),
            "client_settings": {"timeout_s": client_timeout, "retries": client_retries,
                                "fake_server_delay_s": float(os.getenv("CRM_FAKE_DELAY_S", "1.5")),
                                "seed": 7}}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--levels", default="0,0.3,0.8")
    p.add_argument("--runs-per-level", type=int, default=10)
    p.add_argument("--client", default="c001")
    p.add_argument("--runs-dir", default="runs-chaos")
    p.add_argument("--json-out", default="runs-chaos/summary.json")
    args = p.parse_args()

    results = []
    for level in [float(x) for x in args.levels.split(",")]:
        result = run_level(level, args.runs_per_level, args.client, args.runs_dir)
        results.append(result)
        print(json.dumps(result, indent=2))
        sys.stdout.flush()

    total = sum(r.get("runs", 0) for r in results)
    crashes = sum(r.get("crashed", 0) for r in results)
    print(f"\nTOTAL runs={total} crashes={crashes} "
          f"(mock CRM in a separate process, one client per run, seed 7)")
    print("A crash here means an exception escaped the orchestrator; the CLI exit code "
          "only distinguishes ok/degraded (0) from failed (2).")
    os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"summary written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
