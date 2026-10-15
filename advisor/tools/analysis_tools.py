"""Rule engine R1-R8.

Design choices worth knowing before reading the code:

* Rules are pure functions over one client's own history. No population
  thresholds: a wealthy client's normal is not the same as a modest one's.
* R1 uses median/MAD rather than mean/std, because the mean moves with the very
  outliers we are hunting.
* R5 uses a p95 baseline that is *not* robust, so it is computed leave-one-out:
  each transaction is compared with a baseline it is not part of.
* The newest month is excluded from trends (R3): the data set ends mid-month and
  a partial month always looks like a drop.
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from advisor import config
from advisor.contracts import ToolResult
from advisor.tools.base import BaseTool


# ----------------------------------------------------------------- helpers
def median(xs: list[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def mad(xs: list[float], med: float) -> float:
    return float(statistics.median([abs(x - med) for x in xs])) if xs else 0.0


def quantile(xs: list[float], q: float) -> float:
    """Linear interpolation, same convention as numpy's default."""
    if not xs:
        return 0.0
    ordered = sorted(xs)
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo))


def robust_z(x: float, med: float, mad_value: float) -> float:
    if mad_value > 0:
        return (x - med) / (1.4826 * mad_value)
    return 99.0 if x > med * 2 else 0.0


def _date(tx: dict) -> date:
    return date.fromisoformat(tx["date"])


def outflows(transactions: list[dict]) -> list[tuple[dict, float]]:
    """Money leaving the account. Inflows are excluded on purpose: a salary is
    large every month and would otherwise dominate every baseline."""
    return [(t, float(abs(t["amount_idr"]))) for t in transactions if t["amount_idr"] < 0]


# ------------------------------------------------------------------- rules
def rule_r1_robust_zscore(transactions: list[dict]) -> list[dict]:
    rows = outflows(transactions)
    if len(rows) < config.MIN_TX_FOR_ZSCORE:
        return []
    amounts = [a for _, a in rows]
    med, m = median(amounts), mad(amounts, median(amounts))
    hits = []
    for tx, amount in rows:
        z = robust_z(amount, med, m)
        # Statistically unusual is not enough: with a tight spread, an ordinary
        # large purchase can sit many sigmas out. It must also be materially
        # large next to the client's typical outflow. (Added after measuring:
        # see README "Measurements" v1 -> v2.)
        if abs(z) >= config.ROBUST_Z_THRESHOLD and amount >= config.R1_MATERIALITY_MULT * med:
            hits.append({
                "tx_id": tx["tx_id"], "rule": "R1_robust_zscore", "value": round(z, 2),
                "amount_idr": int(amount), "baseline_idr": round(med, 0),
                "counterparty": tx["counterparty"],
                "detail": (f"outflow of Rp {amount:,.0f} is {z:.1f} robust sigma above the "
                           f"client's median outflow (Rp {med:,.0f})"),
            })
    return hits


def rule_r2_category_iqr(transactions: list[dict]) -> list[dict]:
    by_category: dict[str, list[tuple[dict, float]]] = {}
    for tx, amount in outflows(transactions):
        by_category.setdefault(tx["category"], []).append((tx, amount))
    hits = []
    for category, rows in by_category.items():
        if len(rows) < config.MIN_TX_PER_CATEGORY:
            continue
        amounts = [a for _, a in rows]
        q1, q3 = quantile(amounts, 0.25), quantile(amounts, 0.75)
        upper = q3 + config.IQR_MULTIPLIER * (q3 - q1)
        for tx, amount in rows:
            if amount > upper:
                hits.append({
                    "tx_id": tx["tx_id"], "rule": "R2_category_iqr", "value": round(amount / upper, 2),
                    "amount_idr": int(amount), "baseline_idr": round(upper, 0),
                    "counterparty": tx["counterparty"],
                    "detail": (f"{category} outflow of Rp {amount:,.0f} breaks the category fence "
                               f"(Rp {upper:,.0f} = Q3 + {config.IQR_MULTIPLIER}x IQR)"),
                })
    return hits


def rule_r4_structuring(transactions: list[dict]) -> list[dict]:
    threshold = config.STRUCTURING_THRESHOLD_IDR
    hot = [(tx, amount) for tx, amount in outflows(transactions)
           if 0.9 * threshold <= amount < threshold]
    hot.sort(key=lambda pair: pair[0]["date"])
    seen: set[tuple[str, ...]] = set()
    hits = []
    for tx, _ in hot:
        window = [(t, a) for t, a in hot
                  if 0 <= (_date(t) - _date(tx)).days < config.STRUCTURING_WINDOW_DAYS]
        if len(window) < config.STRUCTURING_MIN_COUNT:
            continue
        key = tuple(sorted(t["tx_id"] for t, _ in window))
        if key in seen:
            continue
        seen.add(key)
        group_total = sum(a for _, a in window)
        for t, amount in window:
            hits.append({
                "tx_id": t["tx_id"], "rule": "R4_structuring_window", "value": float(len(window)),
                "amount_idr": int(amount), "baseline_idr": float(threshold),
                "counterparty": t["counterparty"],
                "detail": (f"{len(window)} transfers just under the Rp {threshold:,.0f} reporting "
                           f"threshold inside {config.STRUCTURING_WINDOW_DAYS} days "
                           f"(group total Rp {group_total:,.0f})"),
            })
    return hits


def rule_r5_new_counterparty(transactions: list[dict]) -> list[dict]:
    rows = outflows(transactions)
    if len(rows) < config.MIN_TX_FOR_ZSCORE:
        return []
    first_seen: dict[str, str] = {}
    for tx in sorted(transactions, key=lambda t: t["date"]):
        first_seen.setdefault(tx["counterparty"], tx["tx_id"])
    hits = []
    for tx, amount in rows:
        if first_seen.get(tx["counterparty"]) != tx["tx_id"]:
            continue
        others = [a for t, a in rows if t["tx_id"] != tx["tx_id"]]
        p95 = quantile(others, 0.95)
        if p95 > 0 and amount > config.NEW_COUNTERPARTY_P95_MULT * p95:
            hits.append({
                "tx_id": tx["tx_id"], "rule": "R5_new_counterparty",
                "value": round(amount / p95, 2), "amount_idr": int(amount),
                "baseline_idr": round(p95, 0), "counterparty": tx["counterparty"],
                "detail": (f"first ever transfer to {tx['counterparty']} of Rp {amount:,.0f} - "
                           f"{amount / p95:.1f}x the client's usual upper bound "
                           f"(leave-one-out p95 Rp {p95:,.0f})"),
            })
    return hits


def rule_r6_dormant_reactivation(transactions: list[dict]) -> list[dict]:
    """One dormancy episode = one alert.

    The candidate hits carry a `group_id`: a burst of activity after a quiet
    period is a single event an analyst handles once, not N separate alerts.
    (Measured before this change: the same episode produced up to 7 alerts, of
    which only the injected ones were labelled - noise by construction.)
    """
    rows = sorted(transactions, key=lambda t: t["date"])
    hits = []
    episode = 0
    for previous, current in zip(rows, rows[1:]):
        gap = (_date(current) - _date(previous)).days
        if gap <= config.DORMANT_GAP_DAYS:
            continue
        start = _date(current)
        window = [t for t in rows
                  if 0 <= (_date(t) - start).days < config.DORMANT_ACTIVE_WINDOW_DAYS]
        if len(window) < config.DORMANT_ACTIVE_COUNT:
            continue
        episode += 1
        group_id = f"dormant-{episode}"
        for t in window:
            hits.append({
                "tx_id": t["tx_id"], "group_id": group_id, "rule": "R6_dormant_reactivation",
                "value": float(gap), "amount_idr": int(abs(t["amount_idr"])),
                "baseline_idr": float(gap), "counterparty": t["counterparty"],
                "detail": (f"account was quiet for {gap} days, then {len(window)} transactions "
                           f"within {config.DORMANT_ACTIVE_WINDOW_DAYS} days"),
            })
    return hits


def rule_r7_liquidity_drain(transactions: list[dict], profile: dict) -> list[dict]:
    aum = float(profile.get("aum_idr") or 0)
    if aum <= 0:
        return []
    hits = []
    for tx, amount in outflows(transactions):
        if amount >= 0.5 * aum:
            hits.append({
                "tx_id": tx["tx_id"], "rule": "R7_liquidity_drain", "value": round(amount / aum, 3),
                "amount_idr": int(amount), "baseline_idr": round(aum, 0),
                "counterparty": tx["counterparty"],
                "detail": (f"single outflow of Rp {amount:,.0f} equals {amount / aum:.0%} of the "
                           f"client's Rp {aum:,.0f} AUM"),
            })
    return hits


def rule_r8_concentration(profile: dict) -> list[dict]:
    hits = []
    for holding in profile.get("holdings") or []:
        pct = holding.get("pct")
        if pct is not None and pct > config.CONCENTRATION_PCT:
            hits.append({
                "rule": "R8_concentration", "value": float(pct), "asset": holding.get("asset"),
                "detail": (f"{pct:.0%} of the portfolio sits in {holding.get('asset')} "
                           f"(above the {config.CONCENTRATION_PCT:.0%} concentration line)"),
            })
    return hits


def rule_r3_category_trend(transactions: list[dict]) -> list[dict]:
    """Month-over-month movement per category. Excludes the newest (partial) month."""
    monthly: dict[tuple[str, str], float] = {}
    for tx, amount in outflows(transactions):
        key = (tx["category"], tx["date"][:7])
        monthly[key] = monthly.get(key, 0.0) + amount
    months = sorted({month for _, month in monthly})
    if len(months) < config.MIN_MONTHS_FOR_TREND + 1:
        return []
    current_month = months[-2]           # the last complete month
    history_months = months[-2 - config.MIN_MONTHS_FOR_TREND:-2]
    hits = []
    for category in sorted({c for c, _ in monthly}):
        current = monthly.get((category, current_month), 0.0)
        history = [monthly[(category, m)] for m in history_months if (category, m) in monthly]
        if len(history) < config.MIN_MONTHS_FOR_TREND:
            continue
        baseline = median(history)
        if baseline <= 0 or current / baseline < config.TREND_RATIO:
            continue
        hits.append({
            "category": category, "month": current_month, "value": round(current / baseline, 2),
            "amount_idr": int(current), "baseline_idr": round(baseline, 0),
            "detail": (f"{category} spending in {current_month} was Rp {current:,.0f}, "
                       f"{current / baseline:.1f}x the 6-month median (Rp {baseline:,.0f})"),
        })
    return hits


# ------------------------------------------------------------------- tools
class DetectAnomalies(BaseTool):
    name = "analysis.detect_anomalies"
    description = ("Run the transaction-level rule set (R1, R2, R4, R5, R6, R7) over one "
                   "client's transactions and return candidate hits.")

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object",
                "properties": {"client_id": {"type": "string"},
                               "transactions": {"type": "array"},
                               "profile": {"type": "object"}},
                "required": ["client_id", "transactions"]}

    def _run(self, client_id: str, transactions: list[dict], profile: dict | None = None,
             **_: Any) -> ToolResult:
        profile = profile or {}
        candidates = (rule_r1_robust_zscore(transactions)
                      + rule_r2_category_iqr(transactions)
                      + rule_r4_structuring(transactions)
                      + rule_r5_new_counterparty(transactions)
                      + rule_r6_dormant_reactivation(transactions)
                      + rule_r7_liquidity_drain(transactions, profile))
        rules_run = sum(1 for tx in transactions if tx["amount_idr"] < 0)
        return ToolResult(ok=True, data={"client_id": client_id, "candidates": candidates,
                                         "outflows_considered": rules_run,
                                         "rules_run": ["R1", "R2", "R4", "R5", "R6", "R7"]})


class ComputeTrends(BaseTool):
    name = "analysis.compute_trends"
    description = "Month-over-month spending movement per category (R3)."

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"transactions": {"type": "array"}},
                "required": ["transactions"]}

    def _run(self, transactions: list[dict], **_: Any) -> ToolResult:
        return ToolResult(ok=True, data={"trends": rule_r3_category_trend(transactions)})


class ScoreRisk(BaseTool):
    name = "analysis.score_risk"
    description = "Portfolio-level risk signals (R8)."

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"profile": {"type": "object"}},
                "required": ["profile"]}

    def _run(self, profile: dict, **_: Any) -> ToolResult:
        return ToolResult(ok=True, data={"signals": rule_r8_concentration(profile)})


def build_analysis_tools() -> list[BaseTool]:
    return [DetectAnomalies(), ComputeTrends(), ScoreRisk()]
