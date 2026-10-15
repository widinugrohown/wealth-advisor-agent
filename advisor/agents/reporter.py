"""Reporter Agent.

Two paths, one output shape:

* **LLM path** - the findings are handed to the model as *facts*. The model writes
  the narrative and proposes actions; it is never allowed to invent a finding, a
  transaction id or a number. Every recommendation it returns is validated
  against the finding list before it is accepted.
* **rule-based path** - deterministic prose built from the same findings. This is
  what keeps the repository runnable for a reviewer with no API key.

Anything the LLM returns that fails validation is dropped, the run is labelled
`llm_invalid_json` / `llm_recommendation_dropped`, and the rule-based text is
used instead. A degraded report beats a hallucinated one.
"""
from __future__ import annotations

import json

from advisor import llm_client
from advisor.agents.base import BaseAgent
from advisor.contracts import Recommendation, RunContext

MAX_LLM_RECOMMENDATIONS = 5

SYSTEM_PROMPT = (
    "You are a wealth advisory assistant writing an internal note for a human advisor. "
    "You are given findings that have already been computed by a rule engine. "
    "Rules you must follow: never invent findings, transaction ids, counterparties or numbers; "
    "use only what is given; write in plain English; be specific about the amount and the date "
    "when you mention a transaction; if the data was partly unavailable, say so. "
    "Reply with JSON only: {\"summary_text\": str, \"recommendations\": "
    "[{\"action\": str, \"priority\": int, \"target_id\": str|null, \"rationale\": str, "
    "\"requires_approval\": bool}]}"
)


class ReporterAgent(BaseAgent):
    name = "reporter"

    def __init__(self, tools, logger, use_llm: bool = True):
        super().__init__(tools, logger)
        self.use_llm = use_llm

    def run(self, ctx: RunContext) -> RunContext:
        with self.timed(ctx, "report"):
            fallback_text, fallback_recs = self._rule_based(ctx)
            if not self.use_llm:
                self._log("decision", decision="reporter_mode", mode="rule_based",
                          reason="llm_disabled_by_flag")
                ctx.summary_text, ctx.recommendations = fallback_text, fallback_recs
                return ctx
            if not llm_client.is_configured():
                ctx.degrade("llm_not_configured")
                self._log("decision", level="warn", decision="reporter_mode", mode="rule_based",
                          reason="no_api_key")
                ctx.summary_text, ctx.recommendations = fallback_text, fallback_recs
                return ctx

            try:
                payload, response = llm_client.chat_json(self._messages(ctx))
                ctx.llm_calls += 1
                ctx.llm_tokens_in += response.tokens_in
                ctx.llm_tokens_out += response.tokens_out
            except Exception as exc:  # noqa: BLE001 - any LLM failure falls back
                ctx.degrade(f"llm_error:{type(exc).__name__}")
                self._log("decision", level="error", decision="reporter_fallback",
                          reason=f"{type(exc).__name__}: {exc}")
                ctx.summary_text, ctx.recommendations = fallback_text, fallback_recs
                return ctx

            if not payload or "summary_text" not in payload:
                ctx.degrade("llm_invalid_json")
                self._log("decision", level="warn", decision="reporter_fallback",
                          reason="llm_invalid_json")
                ctx.summary_text, ctx.recommendations = fallback_text, fallback_recs
                return ctx

            recommendations, dropped = self._validate(payload.get("recommendations"), ctx)
            if dropped:
                ctx.degrade(f"llm_recommendations_dropped:{dropped}")
            if not recommendations:
                recommendations = fallback_recs
                ctx.degrade("llm_no_valid_recommendations")

            ctx.summary_text = str(payload["summary_text"]).strip()
            ctx.recommendations = recommendations
            self._log("decision", decision="reporter_mode", mode="llm",
                      n_recommendations=len(recommendations),
                      tokens_in=ctx.llm_tokens_in, tokens_out=ctx.llm_tokens_out)
        return ctx

    # --------------------------------------------------------------- prompts
    def _messages(self, ctx: RunContext) -> list[dict]:
        snapshot = {k: (ctx.profile or {}).get(k) for k in ("name", "risk_profile", "aum_idr")}
        facts = {
            "client": snapshot,
            "status": ctx.status,
            "degraded_reasons": ctx.degraded_reasons,
            "advisor_notes": ctx.advisor_notes,
            "previous_runs": ctx.memories,
            "findings": [{
                "finding_id": f.finding_id,
                "type": f.type,
                "severity": f.severity,
                "rules": f.rules,
                "tx_ids": f.evidence.tx_ids,
                "amount_idr": f.evidence.amount_idr,
                "explained_by_advisor_note": f.evidence.extra.get("explained_by_advisor_note", False),
                "explanation": f.explanation,
            } for f in ctx.findings],
        }
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]

    def _validate(self, raw, ctx: RunContext) -> tuple[list[Recommendation], int]:
        """Keep only recommendations that point at known transactions."""
        known_tx = {tx for f in ctx.findings for tx in f.evidence.tx_ids}
        out: list[Recommendation] = []
        dropped = 0
        for item in raw or []:
            if not isinstance(item, dict) or not item.get("action") or not item.get("rationale"):
                dropped += 1
                continue
            target = item.get("target_id")
            if target is not None and target not in known_tx:
                dropped += 1
                continue
            try:
                priority = int(item.get("priority", 2))
            except (TypeError, ValueError):
                priority = 2
            out.append(Recommendation(action=str(item["action"]),
                                      priority=max(1, min(5, priority)),
                                      target_id=target,
                                      rationale=str(item["rationale"]),
                                      requires_approval=bool(item.get("requires_approval", False))))
        return out[:MAX_LLM_RECOMMENDATIONS], dropped

    # ------------------------------------------------------------ rule-based
    def _rule_based(self, ctx: RunContext) -> tuple[str, list[Recommendation]]:
        profile = ctx.profile or {}
        headline = (f"{profile.get('name', ctx.client_id)} "
                    f"({profile.get('risk_profile', 'unknown')} risk, "
                    f"AUM Rp {int(profile.get('aum_idr') or 0):,}): "
                    f"{len(ctx.findings)} signal(s) found by the rule engine.")
        lines = [headline]

        if ctx.memories:
            previous = ctx.memories[0]
            lines.append(
                f"Compared with the stored run of {previous.get('date', '?')[:10]}: "
                f"findings went from {previous.get('n_findings', '?')} to {len(ctx.findings)} "
                f"(previous worst severity: {previous.get('max_severity', '?')}).")

        for finding in ctx.findings:
            tx = f" ({', '.join(finding.evidence.tx_ids)})" if finding.evidence.tx_ids else ""
            note = (" [already explained in an advisor note]" if
                    finding.evidence.extra.get("explained_by_advisor_note") else "")
            lines.append(f"- {finding.severity.upper()} {finding.finding_id}{tx}: "
                         f"{finding.explanation}{note}")

        if not ctx.findings:
            lines.append("- No rule fired on this client's history.")
        if ctx.degraded_reasons:
            lines.append(f"Data quality: the run is marked degraded ({', '.join(ctx.degraded_reasons)}); "
                         f"some checks may be incomplete.")

        return "\n".join(lines), self._rule_based_recommendations(ctx)

    @staticmethod
    def _rule_based_recommendations(ctx: RunContext) -> list[Recommendation]:
        recs: list[Recommendation] = []
        if ctx.degraded_reasons:
            recs.append(Recommendation(
                action="refresh_client_data", priority=1,
                rationale="Part of the input was unavailable; re-run before acting on the findings.",
                requires_approval=False))
        for finding in ctx.findings:
            if finding.type == "anomaly":
                recs.append(Recommendation(
                    action="review_transaction", priority=1 if finding.severity == "high" else 2,
                    target_id=finding.evidence.tx_ids[0] if finding.evidence.tx_ids else None,
                    rationale=f"{finding.severity.upper()}: {finding.explanation}",
                    requires_approval=finding.severity == "high"))
            elif finding.type == "trend":
                recs.append(Recommendation(
                    action="discuss_spending_trend", priority=3,
                    rationale=finding.explanation, requires_approval=False))
            elif finding.type == "risk":
                recs.append(Recommendation(
                    action="schedule_portfolio_review", priority=3,
                    rationale=finding.explanation, requires_approval=False))
        if not recs:
            recs.append(Recommendation(action="no_action_required", priority=3,
                                       rationale="No rule fired on this client's history.",
                                       requires_approval=False))
        return recs[:MAX_LLM_RECOMMENDATIONS]
