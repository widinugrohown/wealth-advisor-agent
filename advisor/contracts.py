"""Single source of truth for every shape that travels between agents.

Nothing else in the codebase is allowed to invent a dict shape: tools return
ToolResult, agents read and write RunContext, the pipeline emits AdvisoryOutput.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["low", "medium", "high"]
FindingType = Literal["anomaly", "trend", "risk"]
RunStatus = Literal["ok", "degraded", "failed"]

OUTPUT_KEYS = (
    "run_id", "client_id", "generated_at", "status", "client_snapshot",
    "findings", "recommendations", "summary_text", "human_review",
    "degraded_reasons", "metrics",
)


@dataclass
class ToolResult:
    """What every tool returns. A tool never raises; a failure is data."""

    ok: bool
    tool: str = ""
    data: Any = None
    error: str | None = None            # "timeout" | "http_500" | "missing_field:x" | "Exception: ..."
    latency_ms: int = 0
    attempts: int = 1
    degraded_reason: str | None = None  # ok=True but the payload is incomplete

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Evidence:
    """Enough to recompute the finding by hand. Reviewers do check."""

    tx_ids: list[str] = field(default_factory=list)
    amount_idr: int | None = None
    baseline_idr: float | None = None
    value: float | None = None          # the score that fired the rule (z, ratio, count)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    finding_id: str
    type: FindingType
    severity: Severity
    rules: list[str]
    evidence: Evidence
    explanation: str
    confidence: float


@dataclass
class Recommendation:
    action: str
    priority: int
    rationale: str
    target_id: str | None = None
    requires_approval: bool = False


@dataclass
class HumanReview:
    required: bool = False
    decision: str = "skipped"           # approved | overridden | rejected | skipped
    reviewer: str | None = None
    note: str = ""
    decided_at: str | None = None


@dataclass
class AdvisoryOutput:
    run_id: str
    client_id: str
    generated_at: str
    status: RunStatus = "ok"
    client_snapshot: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    summary_text: str = ""
    human_review: HumanReview = field(default_factory=HumanReview)
    degraded_reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def missing_keys(self) -> list[str]:
        return [k for k in OUTPUT_KEYS if k not in self.to_dict()]


@dataclass
class RunContext:
    """Short-term memory: the object handed from agent to agent."""

    run_id: str
    client_id: str
    status: RunStatus = "ok"
    profile: dict[str, Any] | None = None
    transactions: list[dict[str, Any]] = field(default_factory=list)
    advisor_notes: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    summary_text: str = ""
    degraded_reasons: list[str] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    stage_latency_ms: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0

    def degrade(self, reason: str) -> None:
        """Record a partial failure. Called by agents, never raises."""
        if reason not in self.degraded_reasons:
            self.degraded_reasons.append(reason)
        if self.status == "ok":
            self.status = "degraded"

    def fail(self, reason: str) -> None:
        """The run cannot produce a meaningful answer (e.g. the client does not exist)."""
        if reason not in self.degraded_reasons:
            self.degraded_reasons.append(reason)
        self.status = "failed"

    @property
    def has_high_severity(self) -> bool:
        return any(f.severity == "high" for f in self.findings)
