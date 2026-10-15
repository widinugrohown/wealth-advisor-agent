"""Data Fetcher Agent.

Written out in full as the worked example of the pattern: call tools through the
registry, treat every failure as a label, never raise. analyzer.py and
orchestrator.py follow the same shape.
"""
from __future__ import annotations

from advisor.agents.base import BaseAgent
from advisor.contracts import RunContext


class FetcherAgent(BaseAgent):
    name = "fetcher"

    def run(self, ctx: RunContext) -> RunContext:
        with self.timed(ctx, "fetch"):
            profile = self.tools.run("crm.get_profile", client_id=ctx.client_id)
            if profile.ok:
                ctx.profile = profile.data
                if profile.degraded_reason:
                    ctx.degrade(profile.degraded_reason)
            else:
                ctx.profile = {"client_id": ctx.client_id}
                if profile.error == "http_404":
                    # the client does not exist: no amount of retrying produces an analysis
                    ctx.fail(f"crm_http_404:client_profile")
                    self._log("error", level="error", status="error",
                              decision="abort_run", reason="client_not_found")
                    return ctx
                ctx.degrade(f"crm_failed:client_profile:{profile.error}")

            tx = self.tools.run("crm.get_transactions", client_id=ctx.client_id)
            if tx.ok:
                ctx.transactions = sorted(tx.data["transactions"], key=lambda t: t["date"])
            else:
                ctx.transactions = []
                ctx.degrade(f"crm_failed:transactions:{tx.error}")

            notes = self.tools.run("crm.get_notes", client_id=ctx.client_id)
            if notes.ok:
                ctx.advisor_notes = notes.data.get("notes", [])
            else:
                self._log("decision", level="warn", status="error",
                          decision="skip_advisor_notes", reason=notes.error)

            self._log("decision", decision="fetch_complete",
                      n_transactions=len(ctx.transactions), status=ctx.status)
        return ctx
