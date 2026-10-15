# Wealth Advisor Assistant

A small multi-agent system that turns one client's financial data into a
structured, auditable advisory output. An **orchestrator** routes work between a
**Fetcher**, an **Analyzer** and a **Reporter** agent; the agents reach the
outside world only through a **tool registry**; every step is written to a JSONL
event log; high-severity runs stop at a **human checkpoint**; and each run
compares itself with the ones before it through a **memory layer**.

It runs end to end with **no API key** and **no Docker** — the LLM is optional
and only writes the narrative. Everything in this README that looks like a claim
carries the command that produced it, or says `not measured yet`.

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/generate_clients.py --seed 42 --clients 20 --anomalies-per-client 3
bash scripts/run_demo.sh c001 --no-llm --auto-approve      # starts the mock CRM, runs, stops it
```

Output of that last command:

```
== mock CRM on http://127.0.0.1:8077 (CRM_CHAOS=0.0) ==
{"status":"ok","chaos_rate":0.0}
[ok      ] c001: 5 findings (3 high) | hitl=approved | runs/20261015T113641-c001-9ecf/advisory.json
```

A real excerpt from `examples/output_client_001.json`:

```json
{
  "status": "ok",
  "client_snapshot": { "name": "Client 001", "risk_profile": "conservative", "aum_idr": 8862806178 },
  "findings": [
    { "finding_id": "F1", "type": "anomaly", "severity": "low", "rules": ["R6_dormant_reactivation"],
      "evidence": { "tx_ids": ["t0138", "t0139", "t0140"], "value": 147.0 },
      "explanation": "account was quiet for 147 days, then 3 transactions within 7 days" },
    { "finding_id": "F3", "type": "anomaly", "severity": "high",
      "rules": ["R1_robust_zscore", "R5_new_counterparty", "R7_liquidity_drain"],
      "evidence": { "tx_ids": ["t0138"], "amount_idr": 5840716708, "baseline_idr": 3246349, "value": 7746.3 },
      "explanation": "outflow of Rp 5,840,716,708 is 7746.3 robust sigma above the client's median outflow
                      (Rp 3,246,349); first ever transfer to cp_big_001 ... 737.3x the client's usual upper
                      bound (leave-one-out p95 Rp 7,921,646); single outflow ... equals 66% of the client's AUM" }
  ],
  "human_review": { "required": true, "decision": "approved", "reviewer": "auto-approve" },
  "metrics": { "llm_calls": 0, "stage_latency_ms": { "fetch": 27, "analyze": 1, "report": 0 } }
}
```

No API key? Nothing to do. Without `LLM_API_KEY` the reporter uses its
rule-based narrative and logs one `warn` line; the output shape is identical.

---

## a. Architecture

Full diagram, sequence and interface table: `docs/architecture.md`. Summary:

```
main.py CLI
    │
    ▼
Orchestrator ──────────────► memory/insights.jsonl      (long-term memory, append-only)
    │  owns the run, the order, the checkpoint
    ├──► Fetcher  Agent ──┐
    ├──► Analyzer Agent ──┤──► ToolRegistry ──► Mock CRM API (HTTP, may fail)
    └──► Reporter Agent ──┘                 └──► Rule engine R1-R8
                                   │
                                   └──► LLM (optional: narrative + actions only)
    │
    ├──► runs/<run_id>/events.jsonl     one JSON line per event, machine readable
    └──► runs/<run_id>/advisory.json    the deliverable
```

Three seams do the architectural work:

| Seam | Interface | Why it exists |
|---|---|---|
| Agent ↔ outside world | `ToolRegistry` | Agents cannot import `httpx`; a tool can be swapped for a fake in tests; every call is logged by the registry, so no call can go unlogged |
| Tool ↔ failure | `ToolResult(ok, data, error, latency_ms, attempts, degraded_reason)` | A failure is **data**, not an exception. `BaseTool.run()` catches everything and returns a result |
| Agent ↔ agent | `RunContext` | One shared object carries profile, transactions, findings, memories, stage latencies. No globals |

## b. Agent interactions and flow

1. `main.py` builds the registry (3 CRM tools + 3 analysis tools) and the orchestrator.
2. Orchestrator creates a `RunContext`, logs `run_start`, and reads the last 3 stored insights for this client (`memory_read`).
3. **Fetcher** — `crm.get_profile` → `crm.get_transactions` → `crm.get_notes`. A failed call degrades the run instead of ending it; a missing client (HTTP 404) ends it as `failed`; a missing *field* keeps `ok=True` and adds a `degraded_reason`.
4. **Analyzer** — `analysis.detect_anomalies` (R1, R2, R4, R5, R6, R7), `analysis.compute_trends` (R3), `analysis.score_risk` (R8). Candidates merge into findings: several rules on one transaction become **one** finding; an episode rule (R6) becomes **one** alert for the whole window. Advisor notes can **downgrade** a finding, never delete it.
5. **Reporter** — rule-based narrative, or the LLM given the findings as *facts*. The LLM never invents a finding: recommendations pointing at unknown transaction ids are dropped and counted (`llm_recommendations_dropped:n` is a degraded reason).
6. **Human checkpoint** — pauses only when a `high` finding exists, unless `--auto-approve`. `approve` / `override` (replaces the top recommendation with your instruction) / `reject` (clears the recommendations). Recorded in `human_review` and as a `hitl_decision` log line.
7. Output written to `runs/<run_id>/advisory.json`; one line appended to long-term memory; `run_end` logged with the final status.

## c. Key decisions and trade-offs

Full table with the price of each choice: `docs/decisions.md`. The six that matter most:

| Decision | Rejected alternative | Price paid |
|---|---|---|
| Deterministic orchestration; the LLM writes language only | Let the LLM choose every step | Less "agentic" on paper; requires the workflow to be known up front (for wealth advisory, it is) |
| Errors are injected (`CRM_CHAOS`), not described | Prose in the README about "graceful handling" | The mock is hostile by default: demos look worse than reality |
| Robust statistics for R1 (median/MAD) + materiality guard | Mean/std, or a learned model | Thresholds are hand-tuned; no calibrated probabilities; every finding is explainable to an advisor |
| Per-client baselines, never population thresholds | One global threshold table | Needs ≥20 transactions per client; clients below get fewer rules (and the run says so) |
| `ToolResult` everywhere; nothing raises | Let exceptions bubble to the orchestrator | More code per tool, and every tool must decide what "partial success" means |
| No agent framework (LangGraph/CrewAI) in the core | Framework-provided handoffs and retries | Hand-rolled orchestration; the framework comparison is written down instead (`docs/decisions.md`) |

## d. Assumptions

- **All data is synthetic** (20 generated clients, IDR, integers): no client, employer or customer data anywhere in this repository.
- Anomalies are **injected and labelled by the generator**, so the precision/recall below is **optimistic by construction** — it measures whether the rules behave as specified, not real-world detection power.
- One client = one portfolio, one currency, one advisor.
- Python 3.10+; the mock CRM must be reachable at `CRM_BASE_URL` (default `http://127.0.0.1:8077`).
- Docker is offered as an optional extra, never the only way to run anything.

## e. How to run

```bash
make install                       # pip install -r requirements.txt
make data                          # 20 clients + labelled ground truth (seed 42)
make crm                           # terminal 1: uvicorn mock_crm.server:app --port 8077
make demo                          # terminal 2: one client, interactive checkpoint
make demo-no-llm                   # same, rule-based reporter
make test                          # pytest -q
make all && make eval              # run every client, then score against the ground truth
make latency                       # p50/p95 per stage from the event logs
make matrix                        # 30 runs against a mock CRM that fails on purpose
python scripts/make_examples.py    # regenerate the files in examples/
```

Trigger the failure paths by hand:

```bash
curl -s -H 'X-Chaos: http_500'   $CRM_BASE_URL/clients/c001 | head -c 120
curl -s -H 'X-Chaos: missing_field' $CRM_BASE_URL/clients/c001 | head -c 120
CRM_CHAOS=0.3 bash scripts/run_demo.sh c001 --auto-approve
CRM_CHAOS=0.8 bash scripts/run_demo.sh c001 --auto-approve
```

---

## Detection rules

Each rule is a pure function over **one client's own history** (`advisor/tools/analysis_tools.py`),
with its thresholds in `advisor/config.py` so they can be tuned one at a time.

| ID | Rule | Minimum data | Threshold |
|---|---|---|---|
| R1 | Robust z-score of an outflow: `z = (x − median) / (1.4826 × MAD)`, **and** `x ≥ 3 × median` | ≥20 outflows | \|z\| ≥ 3.5 and material |
| R2 | Category fence: `Q3 + 3 × IQR` within the category | ≥8 outflows in that category | above the fence |
| R3 | Category trend: this month vs the median of the previous 6 | ≥6 complete months | ratio ≥ 2.5 |
| R4 | Structuring: ≥3 outflows within 7 days each in `[0.9 × T, T)` | — | T = Rp 10,000,000 |
| R5 | First-ever transfer to a counterparty, `> 3 × leave-one-out p95` | ≥20 outflows | — |
| R6 | Dormancy: a quiet period > 90 days, then ≥3 transactions within 7 days | — | one alert per episode |
| R7 | Liquidity drain: a single outflow ≥ 50% of AUM | AUM known | always `high` |
| R8 | Concentration: one asset > 80% of the portfolio (`risk`, not `anomaly`) | holdings present | `low` |

Inflows are excluded from R1/R2/R5 on purpose: a monthly salary is large every
month and would dominate any baseline. The newest month is excluded from R3: the
data set ends mid-month and a partial month always looks like a drop.

## Measurements

Environment: Linux, Python 3.14.7, `--no-llm` unless stated. Every figure below
was produced by the command in its row; raw summaries are in `runs/eval_summary.json`
and `runs-chaos/summary.json` (both gitignored — re-run the commands to reproduce).

| Metric | How | Result |
|---|---|---|
| Test suite | `pytest -q` (also `make test`) | **42 passed** |
| Anomaly detection (per transaction) | `make all && make eval` | precision **0.629**, recall **0.947**, F1 **0.756** (TP 90, FP 53, FN 5) |
| Anomaly detection (per alert) | same run | **0.669** — 136 alerts, 91 point at a labelled transaction |
| Findings explained by an advisor note | same run | 26 downgraded, 0 deleted |
| Stage latency, rule-based | `make latency` (20–21 runs) | fetch p50 **25 ms**, p95 **37–81 ms** across repeats · analyze p50 **1 ms** / p95 2–3 ms · report p50 **0 ms**. The median is stable, the far tail is not: a 21-sample p95 is the second-largest value, so it moves with the CRM process startup |
| Stage latency, LLM path | 2 runs with `LLM_API_KEY` set | report **15,863 ms** and **36,697 ms**; 1 call per run; 1143/751 and 1924/1359 tokens |
| Failure resilience | `make matrix` (3 levels × 10 runs) | 30 runs, **0 crashes**; ok/degraded = 10/0 at chaos 0.0, 7/3 at 0.3, 1/9 at 0.8 |
| Human checkpoint | 20 baseline runs | 20/20 runs had a `high` finding and therefore reached the checkpoint |
| Memory actually used | `examples/output_client_001_memory_run2.json` | run 2 reports `memory_insights_used: 1` and a "Compared with the stored run of ..." line |
| Override path | `examples/output_client_001_overridden.json` | `recommendations[0].action = human_override`, note recorded in `human_review` |

### How the detector got there (one change per measurement)

| # | Change | Precision | Recall | F1 |
|---|---|---|---|---|
| 1 | first run, as written | 0.148 | 0.980 | 0.257 |
| 2 | ground truth corrected: every transaction of a burst labelled, not just the last | 0.305 | 0.963 | 0.463 |
| 3 | **one threshold change**: R1 now needs `unusual AND material` (`x ≥ 3 × median`) | 0.690 | 0.832 | 0.754 |
| 4 | generator's dormancy story made real (it was not dormant) + R6 grouped into one alert per episode | 0.629 | 0.947 | 0.756 |

Step 3 was the single biggest win. Step 4 **cost** 0.06 precision to buy back 0.11
recall: fixing the dormancy data removed ordinary transactions and therefore moved
the baselines. It is reported as measured, not re-tuned until the headline looked
better.

### Where the remaining errors are

- **40 of the 53 false-positive transactions are exactly the 40 hard negatives**: the
  annual insurance premium (Rp 15–25 m) and the pre-explained family transfer
  (Rp 12–30 m) — two per client, generated on purpose as *legitimate but unusual* and
  never labelled. The system flags them; an advisor decides. That is intended
  behaviour, and it is why precision is not 1.00 on synthetic data.
- **5 more are single-transaction R1 alerts** on clients whose ordinary spending is
  small (Rp 8.9 m, 8.6 m, 8.7 m, 8.7 m, 8.9 m against medians of Rp 1.3–1.8 m): unusual
  *and* material by this client's own history.
- The last **8 are ordinary transactions inside a dormancy window** that also contained
  a genuine burst. The alert is correct; the window is simply wider than the injected
  pattern, so the transactions that share it are counted against precision.
- **All 5 misses have a specific cause**, checked one by one in the data
  (`c001`, `c020`):
  - **3 misses (c001)**: the dormancy episode anchors on the *first* transaction after
    the quiet period. In this client another injected transaction arrived first, so the
    7-day window closed before the intended burst (Rp 5.4–7.1 m) — which is also below
    R1's materiality line and outside R4's structuring band. Fix would be to score the
    whole quiet→activity transition, not the first week of it.
  - **2 misses (c020)**: Rp 8.27 m and Rp 7.81 m against a materiality line of
    Rp 8.29 m — 0.2% and 5.8% below a threshold that is a fixed multiple of the median.
    This is the price of step 3 above, visible in the numbers rather than hidden.

## What I would do next

- Run the pipeline as a scheduled job over a real, consented data set and re-measure
  — the current numbers describe the rules, not the world.
- Move the human checkpoint from a terminal prompt to an approval queue, with the
  same `hitl_decision` record.
- Replace the JSONL memory with a store that can answer "what changed since March",
  not just "the last 3 runs".
- Add calibration: turn rule hits into a probability and choose thresholds from the
  cost of a missed alert vs a false one.
- Exercise the optional `--agentic` path (LLM tool selection through
  `ToolRegistry.specs()`) and measure whether it beats the fixed pipeline. Today it
  is wired but unmeasured.

## Limitations

- Thresholds are hand-tuned on synthetic data and have **not** been validated on real
  client data. Nothing here is a fraud-detection claim.
- The labelled data is optimistic by construction (the generator knows what it hid).
- Detectors are per-client; a client with fewer than 20 outflows gets fewer rules, and
  the run records that instead of pretending otherwise.
- **The LLM narrative is not verified by construction.** In one recorded run the prose
  said *"Three of them (F1, F5) are explained by recent advisor notes"* — two ids, a
  count of three. Findings are rule-derived and auditable; the prose can still be
  wrong, and only length/structure checks are in place. The rule-based path has no
  such failure mode, which is why it is the default.
- No UI, no database, no authentication: assignment-scale, on purpose.

## Layout

```
main.py                  CLI entry point (exit 0 = ok/degraded, 2 = failed)
advisor/
  contracts.py           ToolResult, Evidence, Finding, Recommendation, AdvisoryOutput, RunContext
  config.py              every threshold, in one place
  logging_setup.py       JSONL event logger
  llm_client.py          the only door to an LLM (provider-agnostic)
  memory.py              short-term = RunContext, long-term = insights.jsonl
  orchestrator.py        order of agents, memory, human checkpoint, final status
  agents/                base.py, fetcher.py, analyzer.py, reporter.py
  tools/                 base.py (BaseTool, ToolRegistry), crm_tools.py, analysis_tools.py
mock_crm/                FastAPI CRM with injectable failures (chaos.py)
scripts/                 generate_clients, evaluate_anomaly, latency_report, run_chaos_matrix,
                         make_examples, run_demo.sh
tests/                   contract, tools, rules, orchestrator error paths, generator invariants
docs/                    architecture.md, decisions.md
examples/                input, three sample outputs (plain, memory run 2, overridden) and the LLM run
```

Built with an AI assistant as a pair-programming tool; every decision and every
number in this README was reviewed and reproduced by me.
