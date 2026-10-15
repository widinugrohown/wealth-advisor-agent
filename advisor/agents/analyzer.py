"""Analyzer Agent.

Turns tool output into `Finding` objects. Two things it does that are easy to
get wrong and are tested:

1. Several rules firing on the same transaction become ONE finding with several
   rule names - otherwise four rules on one transaction destroy precision.
2. Advisor free-text notes can *downgrade* a finding (the client already
   explained it) but never delete it. The analyst still wants to see it.
"""
from __future__ import annotations

from advisor.agents.base import BaseAgent
from advisor.contracts import Evidence, Finding, RunContext

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}


class AnalyzerAgent(BaseAgent):
    name = "analyzer"

    def run(self, ctx: RunContext) -> RunContext:
        with self.timed(ctx, "analyze"):
            if not ctx.transactions:
                self._log("decision", level="warn", decision="no_transactions_to_analyze")
                ctx.degrade("no_transactions")
                return ctx

            detected = self.tools.run("analysis.detect_anomalies", client_id=ctx.client_id,
                                      transactions=ctx.transactions, profile=ctx.profile or {})
            if not detected.ok:
                ctx.degrade(f"analysis_failed:detect_anomalies:{detected.error}")
                self._log("decision", level="error", decision="skip_detection", reason=detected.error)
                return ctx

            trends = self.tools.run("analysis.compute_trends", transactions=ctx.transactions)
            signals = self.tools.run("analysis.score_risk", profile=ctx.profile or {})

            ctx.findings = (
                self._merge_transaction_hits(ctx, detected.data["candidates"])
                + self._trend_findings(trends.data["trends"] if trends.ok else [])
                + self._risk_findings(signals.data["signals"] if signals.ok else [])
            )
            self._log("decision", decision="analysis_complete",
                      n_findings=len(ctx.findings),
                      n_high=sum(1 for f in ctx.findings if f.severity == "high"))
        return ctx

    # ------------------------------------------------------------- merging
    def _merge_transaction_hits(self, ctx: RunContext, candidates: list[dict]) -> list[Finding]:
        """One finding per transaction - or per group, when a rule reports a pattern.

        Several rules on one transaction merge into one finding (otherwise four
        rules on one transaction destroy precision). A rule that reports an
        episode (R6) supplies a `group_id`, so the whole episode becomes one
        alert instead of one alert per transaction in the window.
        """
        by_key: dict[str, list[dict]] = {}
        for hit in candidates:
            by_key.setdefault(hit.get("group_id") or hit["tx_id"], []).append(hit)

        tx_by_id = {t["tx_id"]: t for t in ctx.transactions}
        findings: list[Finding] = []
        for _, group in sorted(by_key.items()):
            tx_ids = sorted({h["tx_id"] for h in group})
            rules = sorted({h["rule"] for h in group})
            z = max((abs(h["value"]) for h in group if h["rule"] == "R1_robust_zscore"), default=0.0)
            severity = self._severity(rules, z)
            amount = max((h["amount_idr"] for h in group), default=None)
            explanations = list(dict.fromkeys(h["detail"] for h in group))
            explained = self._explained_by_note(ctx, [tx_by_id[t] for t in tx_ids if t in tx_by_id],
                                                group)
            if explained:
                severity = DOWNGRADE[severity]
            findings.append(Finding(
                finding_id=f"F{len(findings) + 1}",
                type="anomaly",
                severity=severity,
                rules=rules,
                evidence=Evidence(
                    tx_ids=tx_ids, amount_idr=amount,
                    baseline_idr=group[0].get("baseline_idr"), value=round(z, 2) or None,
                    extra={"rule_values": {h["rule"]: h["value"] for h in group},
                           "counterparty": group[0].get("counterparty"),
                           "explained_by_advisor_note": explained}),
                explanation="; ".join(explanations),
                confidence=round(min(0.95, 0.55 + 0.15 * len(rules)), 2),
            ))
        return findings

    @staticmethod
    def _severity(rules: list[str], z: float) -> str:
        if "R7_liquidity_drain" in rules or "R4_structuring_window" in rules:
            return "high"
        if "R1_robust_zscore" in rules and z >= 5.0:
            return "high"
        return "medium"

    def _explained_by_note(self, ctx: RunContext, txs: list[dict],
                           group: list[dict]) -> bool:
        """A free-text advisor note can explain any transaction in the finding."""
        counterparties = {h.get("counterparty") for h in group}
        for note in ctx.advisor_notes:
            for counterparty in counterparties:
                if counterparty and counterparty in note:
                    self._log("decision", decision="finding_explained_by_advisor_note",
                              tx_ids=[t["tx_id"] for t in txs], counterparty=counterparty)
                    return True
        return False

    # ---------------------------------------------------------- other types
    @staticmethod
    def _trend_findings(trends: list[dict]) -> list[Finding]:
        return [Finding(finding_id=f"T{i}", type="trend", severity="medium",
                        rules=["R3_category_trend"],
                        evidence=Evidence(amount_idr=t["amount_idr"],
                                          baseline_idr=t["baseline_idr"], value=t["value"],
                                          extra={"category": t["category"], "month": t["month"]}),
                        explanation=t["detail"], confidence=0.7)
                for i, t in enumerate(trends, start=1)]

    @staticmethod
    def _risk_findings(signals: list[dict]) -> list[Finding]:
        return [Finding(finding_id=f"R{i}", type="risk", severity="low",
                        rules=["R8_concentration"],
                        evidence=Evidence(value=s["value"],
                                          extra={"asset": s.get("asset")}),
                        explanation=s["detail"], confidence=0.8)
                for i, s in enumerate(signals, start=1)]
