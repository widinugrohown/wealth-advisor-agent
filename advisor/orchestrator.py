"""Orchestrator: the only place that knows the order of things.

It owns the run, hands one shared `RunContext` from agent to agent, reads and
writes long-term memory, stops at the human checkpoint, and always finishes with
a status (`ok` / `degraded` / `failed`) instead of an exception.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

from advisor import memory
from advisor.agents.analyzer import AnalyzerAgent
from advisor.agents.fetcher import FetcherAgent
from advisor.agents.reporter import ReporterAgent
from advisor.contracts import AdvisoryOutput, HumanReview, RunContext
from advisor.logging_setup import get_logger, log_event
from advisor.tools.base import ToolRegistry


def make_run_id(client_id: str) -> str:
    return f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{client_id}-{uuid.uuid4().hex[:4]}"


class Orchestrator:
    def __init__(self, tools: ToolRegistry, *, runs_dir: str = "runs", use_llm: bool = True,
                 auto_approve: bool = False, interactive: bool = True, memory_path: str | None = None,
                 memory_window: int = 3):
        self.tools = tools
        self.runs_dir = runs_dir
        self.use_llm = use_llm
        self.auto_approve = auto_approve
        self.interactive = interactive
        self.memory_path = memory_path
        self.memory_window = memory_window

    # ------------------------------------------------------------------ run
    def run_client(self, client_id: str, run_id: str | None = None) -> AdvisoryOutput:
        run_id = run_id or make_run_id(client_id)
        logger = get_logger(run_id=run_id, runs_dir=self.runs_dir)
        logger.run_id = run_id
        self.tools.logger = logger          # tool calls from here on land in this run's log
        ctx = RunContext(run_id=run_id, client_id=client_id)
        log_event(logger, "run_start", agent="orchestrator", client_id=client_id)

        ctx.memories = memory.recent_insights(client_id, self.memory_window, self.memory_path)
        log_event(logger, "decision", agent="orchestrator",
                  decision="memory_read", n_insights=len(ctx.memories),
                  status="ok" if ctx.memories else "empty")

        try:
            FetcherAgent(self.tools, logger).run(ctx)
            if ctx.status != "failed":
                AnalyzerAgent(self.tools, logger).run(ctx)
                ReporterAgent(self.tools, logger, use_llm=self.use_llm).run(ctx)
        except Exception as exc:  # noqa: BLE001 - a crashed agent must not lose the run
            ctx.degrade(f"agent_crash:{type(exc).__name__}")
            log_event(logger, "error", agent="orchestrator", level="error",
                      status="error", error=f"{type(exc).__name__}: {exc}")

        review = self._human_checkpoint(ctx, logger)
        output = self._build_output(ctx, review)

        if ctx.status == "failed":
            log_event(logger, "run_end", agent="orchestrator", status=output.status,
                      n_findings=0, n_recommendations=0,
                      degraded_reasons=output.degraded_reasons)
            return output

        stored = memory.remember(ctx, output.summary_text.splitlines()[0], self.memory_path)
        log_event(logger, "decision", agent="memory", decision="memory_write",
                  run_id=stored["run_id"], n_findings=stored["n_findings"])
        log_event(logger, "run_end", agent="orchestrator", status=output.status,
                  n_findings=len(output.findings),
                  n_recommendations=len(output.recommendations),
                  degraded_reasons=output.degraded_reasons)
        return output

    # ---------------------------------------------------------- human in the loop
    def _human_checkpoint(self, ctx: RunContext, logger: logging.Logger) -> HumanReview:
        if not ctx.has_high_severity:
            log_event(logger, "decision", agent="orchestrator",
                      decision="hitl_skipped", reason="no_high_severity_finding")
            return HumanReview(required=False, decision="skipped")
        if self.auto_approve or not self.interactive:
            log_event(logger, "hitl_prompt", agent="orchestrator", status="auto",
                      n_high=sum(1 for f in ctx.findings if f.severity == "high"))
            return HumanReview(required=True, decision="approved", reviewer="auto-approve",
                               note="--auto-approve: checkpoint recorded but not prompted")

        print("\n--- human checkpoint: this run has high-severity findings ---")
        for finding in ctx.findings:
            if finding.severity == "high":
                print(f"  {finding.finding_id} [{finding.type}] {finding.explanation}")
        log_event(logger, "hitl_prompt", agent="orchestrator",
                  n_high=sum(1 for f in ctx.findings if f.severity == "high"))
        answer = input("  [a]pprove  [o]verride  [r]eject ? ").strip().lower() or "a"
        note = ""
        if answer.startswith("o"):
            note = input("  your instruction (replaces the top recommendation): ").strip()
            decision = "overridden"
        elif answer.startswith("r"):
            decision = "rejected"
        else:
            decision = "approved"

        review = HumanReview(required=True, decision=decision, reviewer=os.getenv("USER", "analyst"),
                             note=note, decided_at=datetime.now().astimezone().isoformat(
                                 timespec="seconds"))
        if decision == "overridden" and ctx.recommendations and note:
            top = ctx.recommendations[0]
            top.action, top.rationale = "human_override", f"Human override: {note}"
        if decision == "rejected":
            ctx.recommendations = []
        log_event(logger, "hitl_decision", agent="orchestrator", status="ok",
                  decision=decision, reviewer=review.reviewer, note=note)
        return review

    # ---------------------------------------------------------------- output
    @staticmethod
    def _build_output(ctx: RunContext, review: HumanReview) -> AdvisoryOutput:
        profile = ctx.profile or {}
        return AdvisoryOutput(
            run_id=ctx.run_id,
            client_id=ctx.client_id,
            generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            status=ctx.status,
            client_snapshot={k: profile.get(k) for k in ("name", "risk_profile", "aum_idr")},
            findings=ctx.findings,
            recommendations=ctx.recommendations,
            summary_text=ctx.summary_text,
            human_review=review,
            degraded_reasons=ctx.degraded_reasons,
            metrics={
                "llm_calls": ctx.llm_calls,
                "llm_tokens_in": ctx.llm_tokens_in,
                "llm_tokens_out": ctx.llm_tokens_out,
                "stage_latency_ms": ctx.stage_latency_ms,
                "memory_insights_used": len(ctx.memories),
            },
        )
