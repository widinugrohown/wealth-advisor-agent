"""CLI entry point.

    python main.py --client c001                     one client, LLM if a key exists
    python main.py --all --no-llm --auto-approve      every client, rule-based only
    python main.py --client c001 --no-llm             interactive human checkpoint

Exit codes: 0 = ok/degraded, 2 = failed (e.g. the client does not exist).
The CLI is the only place in the codebase allowed to print for humans.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from advisor.orchestrator import Orchestrator
from advisor.tools.analysis_tools import build_analysis_tools
from advisor.tools.base import ToolRegistry
from advisor.tools.crm_tools import build_crm_tools

EXIT_CODES = {"ok": 0, "degraded": 0, "failed": 2}


def client_ids(data_dir: str) -> list[str]:
    folder = os.path.join(data_dir, "clients")
    if not os.path.isdir(folder):
        return []
    return sorted(name[:-len(".json")] for name in os.listdir(folder) if name.endswith(".json"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Wealth Advisor Assistant - multi-agent run")
    p.add_argument("--client", help="client id, e.g. c001")
    p.add_argument("--all", action="store_true", help="run every client found in data/clients")
    p.add_argument("--no-llm", action="store_true", help="force the rule-based reporter")
    p.add_argument("--auto-approve", action="store_true", help="record the checkpoint without prompting")
    p.add_argument("--quiet", action="store_true", help="write files but print nothing")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--runs-dir", default="runs")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.all and not args.client:
        build_parser().error("pass --client <id> or --all")

    clients = client_ids(args.data_dir) if args.all else [args.client]
    orchestrator = Orchestrator(
        ToolRegistry(build_crm_tools() + build_analysis_tools()),
        runs_dir=args.runs_dir,
        use_llm=not args.no_llm,
        auto_approve=args.auto_approve,
        interactive=not args.auto_approve,
    )

    worst = 0
    llm_hint = False
    for client_id in clients:
        output = orchestrator.run_client(client_id)
        llm_hint = llm_hint or "llm_not_configured" in output.degraded_reasons
        run_dir = os.path.join(args.runs_dir, output.run_id)
        os.makedirs(run_dir, exist_ok=True)
        path = os.path.join(run_dir, "advisory.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(output.to_dict(), fh, indent=2, ensure_ascii=False)
        if not args.quiet:
            high = sum(1 for f in output.findings if f.severity == "high")
            print(f"[{output.status:8}] {client_id}: {len(output.findings)} findings "
                  f"({high} high) | hitl={output.human_review.decision} | {path}")
        worst = max(worst, EXIT_CODES.get(output.status, 2))

    if llm_hint and not args.quiet:
        print("hint: no LLM_API_KEY found. The report was produced by the rule-based path; "
              "pass --no-llm to mark the run as intentionally rule-based (status becomes ok "
              "instead of degraded).")
    return worst


if __name__ == "__main__":
    sys.exit(main())
