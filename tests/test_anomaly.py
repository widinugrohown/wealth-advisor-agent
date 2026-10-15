"""Rule-level tests: one known series per rule, then the merge behaviour.

Each test builds the smallest history that isolates one rule, so a failure names
the rule that broke instead of pointing at the whole detector.
"""
import logging

from advisor.agents.analyzer import AnalyzerAgent
from advisor.contracts import RunContext
from advisor.tools.analysis_tools import (build_analysis_tools, rule_r1_robust_zscore,
                                          rule_r2_category_iqr, rule_r4_structuring,
                                          rule_r5_new_counterparty, rule_r6_dormant_reactivation,
                                          rule_r7_liquidity_drain, rule_r8_concentration)
from advisor.tools.base import ToolRegistry

NORMAL = [{"tx_id": f"n{i:03d}", "date": f"2026-0{1 + i % 9}-{1 + i % 27:02d}",
           "amount_idr": -(3_000_000 + i * 10_000), "category": "groceries",
           "counterparty": f"cp{i % 5:03d}", "channel": "mobile", "note": ""}
          for i in range(30)]


def tx(tx_id, date, amount, category="transfer_out", counterparty="cp_new_001"):
    return {"tx_id": tx_id, "date": date, "amount_idr": amount, "category": category,
            "counterparty": counterparty, "channel": "mobile", "note": ""}


def analyzer():
    return AnalyzerAgent(ToolRegistry(build_analysis_tools()), logging.getLogger("test"))


def test_r1_flags_one_outlier_and_ignores_inflows():
    transactions = NORMAL + [tx("t900", "2026-09-30", -60_000_000),
                             tx("t901", "2026-09-30", 200_000_000, category="salary",
                                counterparty="employer")]
    flagged = {hit["tx_id"] for hit in rule_r1_robust_zscore(transactions)}
    assert flagged == {"t900"}, "the salary is an inflow and must not become a baseline problem"


def test_r1_stays_silent_below_the_minimum_sample():
    assert rule_r1_robust_zscore(NORMAL[:5]) == []


def test_r2_breaks_the_category_fence():
    dining = [tx(f"d{i:02d}", f"2026-08-{i + 1:02d}", -(2_000_000 + i * 1_000), "dining", "cpd01")
              for i in range(10)]
    hits = rule_r2_category_iqr(dining + [tx("dBIG", "2026-08-20", -20_000_000, "dining", "cpd01")])
    assert {h["tx_id"] for h in hits} == {"dBIG"}


def test_r4_catches_the_structuring_burst():
    burst = [tx(f"s{i}", f"2026-09-1{i}", -9_500_000, counterparty="cp_struct_001") for i in range(3)]
    hits = rule_r4_structuring(NORMAL + burst)
    assert {h["tx_id"] for h in hits} == {"s0", "s1", "s2"}


def test_r4_ignores_ordinary_transfers_of_the_same_size_outside_the_window():
    spaced = [tx(f"s{i}", f"2026-0{i + 1}-05", -9_500_000, counterparty="cp_struct_001")
              for i in range(3)]
    assert rule_r4_structuring(NORMAL + spaced) == []


def test_r5_flags_the_first_transfer_to_a_new_counterparty():
    hits = rule_r5_new_counterparty(NORMAL + [tx("t900", "2026-09-30", -40_000_000)])
    assert {h["tx_id"] for h in hits} == {"t900"}


def test_r5_baseline_excludes_the_transaction_under_test():
    """The p95 baseline is not robust, so the transaction being tested is left out of it."""
    transactions = NORMAL + [tx("t900", "2026-09-28", -40_000_000, counterparty="cp_new_001")]
    hits = {h["tx_id"]: h for h in rule_r5_new_counterparty(transactions)}
    assert set(hits) == {"t900"}
    assert hits["t900"]["baseline_idr"] < 5_000_000, "t900 must not be inside its own baseline"


def test_r6_catches_dormancy_then_activity():
    transactions = [tx(f"o{i}", f"2026-0{i + 1}-05", -3_000_000, "groceries", "cp001")
                    for i in range(5)]
    reactivation = [tx(f"w{i}", f"2026-10-0{i + 1}", -6_000_000, counterparty="cp_dorm_001")
                    for i in range(3)]
    hits = rule_r6_dormant_reactivation(transactions + reactivation)
    assert {h["tx_id"] for h in hits} == {"w0", "w1", "w2"}


def test_r7_flags_a_single_outflow_over_half_of_aum():
    hits = rule_r7_liquidity_drain([tx("t900", "2026-09-30", -600_000_000)],
                                   {"aum_idr": 1_000_000_000})
    assert [h["tx_id"] for h in hits] == ["t900"]


def test_r7_silent_when_aum_is_unknown():
    assert rule_r7_liquidity_drain([tx("t900", "2026-09-30", -600_000_000)], {}) == []


def test_r8_flags_concentration():
    hits = rule_r8_concentration({"holdings": [{"asset": "equity_fund", "pct": 0.88},
                                               {"asset": "gold", "pct": 0.12}]})
    assert hits and hits[0]["asset"] == "equity_fund"


def test_two_rules_on_one_transaction_merge_into_one_finding():
    burst = [tx(f"s{i}", f"2026-09-1{i}", -9_600_000, counterparty="cp_struct_001") for i in range(3)]
    ctx = RunContext(run_id="r1", client_id="c001", profile={"aum_idr": 1_000_000_000})
    ctx.transactions = NORMAL + burst
    analyzer().run(ctx)
    anomalies = [f for f in ctx.findings if f.type == "anomaly"]
    assert len(anomalies) == 3, "three transactions, three findings - not six"
    assert all(len(f.rules) == 2 for f in anomalies)
    assert all("R1_robust_zscore" in f.rules and "R4_structuring_window" in f.rules
               for f in anomalies)
    assert all(f.severity == "high" for f in anomalies)


def test_advisor_note_downgrades_but_does_not_delete():
    ctx = RunContext(run_id="r2", client_id="c001", profile={"aum_idr": 1_000_000_000})
    ctx.transactions = NORMAL + [tx("t900", "2026-09-30", -60_000_000)]
    ctx.advisor_notes = ["2026-09-29: client called ahead about a transfer to cp_new_001."]
    analyzer().run(ctx)
    finding = next(f for f in ctx.findings if f.evidence.tx_ids == ["t900"])
    assert finding.evidence.extra["explained_by_advisor_note"] is True
    assert finding.severity == "medium", "high was downgraded"
    assert "R1_robust_zscore" in finding.rules, "the finding is still reported"


def test_dormant_episode_becomes_one_alert(tmp_path):
    """One episode = one finding, however many transactions are in the window."""
    quiet = [tx(f"o{i}", f"2026-0{i + 1}-05", -3_000_000, "groceries", "cp001") for i in range(4)]
    burst = [tx(f"w{i}", f"2026-10-0{i + 1}", -6_000_000, counterparty="cp_dorm_001")
             for i in range(3)]
    ctx = RunContext(run_id="r4", client_id="c001", profile={"aum_idr": 1_000_000_000})
    ctx.transactions = quiet + burst
    analyzer().run(ctx)
    dormancy = [f for f in ctx.findings if "R6_dormant_reactivation" in f.rules]
    assert len(dormancy) == 1, "a burst after a quiet period is one event, not N alerts"
    assert dormancy[0].evidence.tx_ids == ["w0", "w1", "w2"]


def test_analyzer_degrades_when_there_are_no_transactions():
    ctx = RunContext(run_id="r3", client_id="c001")
    analyzer().run(ctx)
    assert ctx.status == "degraded"
    assert "no_transactions" in ctx.degraded_reasons
