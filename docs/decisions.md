# Key decisions and the price paid for each

An entry is only valid if it names the alternative that was rejected and what the
choice cost. Rows marked *(measured)* have a number behind them in the README.

| # | Decision | Rejected alternative | Price / downside |
|---|---|---|---|
| D1 | Deterministic orchestration; the LLM writes language only | Let the LLM choose every step (autonomous loop) | Less "agentic" on paper; needs the workflow known up front — for wealth advisory it is. Also means the LLM cannot silently skip a check |
| D2 | `--no-llm` rule-based reporter is a first-class path | Require an API key to run anything | Two reporters to keep in agreement. *(measured)* LLM path is ~1000× slower: report stage 15.9 s / 36.7 s vs 0 ms |
| D3 | Every tool returns `ToolResult`; nothing raises | Let exceptions bubble to the orchestrator | More code per tool, and each tool must decide what "partial success" means (a missing field degrades, a 404 fails) |
| D4 | Failures are injected (`CRM_CHAOS`), not described | Document "graceful error handling" in prose | The mock is hostile; demos look worse than reality. *(measured)* 30 runs: 0 crashes, ok/degraded 10/0 → 7/3 → 1/9 |
| D5 | Personal baselines (each client against their own history) | Population-level thresholds | A client below 20 outflows gets fewer rules. A wealthier client's normal is not another's anomaly — this is the point |
| D6 | Robust statistics for R1: median/MAD + a materiality guard | Mean/std, or a learned model (isolation forest) | Thresholds are hand-tuned and uncalibrated; no probabilities. *(measured)* the guard took precision 0.305 → 0.690 and cost recall 0.963 → 0.832; two misses now sit 0.2% and 5.8% under the line |
| D7 | Leave-one-out p95 for R5 | Plain p95 over all outflows | Costlier to compute and still not robust to *earlier* outliers: an earlier huge transfer inflates the baseline for a later one. Known limitation, not yet fixed |
| D8 | Synthetic data with injected, labelled anomalies | Real or public transaction data | *(measured)* precision/recall is optimistic by construction — the generator knows what it hid. Compensated with hard negatives that are never labelled |
| D9 | Long-term memory = append-only JSONL, last 3 runs per client | Vector store / embeddings | No semantic recall, only "the recent past for this client". No extra dependency either |
| D10 | HITL blocks only on `severity=high` | Block on every run | *(measured)* 20/20 baseline runs hit the checkpoint, so `--auto-approve` exists for automation and records itself as `reviewer: auto-approve` |
| D11 | Alerts are pattern-scoped, not transaction-scoped | One alert per transaction always | A transaction can appear in two alerts when two patterns cover it (a dormancy burst and an outsized transfer). Honest noise, but it makes alert precision differ from transaction precision |
| D12 | In-process package layout, no Docker in the main path | Docker/compose as the only way to run | Reviewer needs Python 3.10+. Docker is offered as an extra, never a requirement |
| D13 | Prose from the LLM is not treated as evidence | Ask the model to "verify" its own narrative | *(measured)* one run wrote "Three of them (F1, F5)" — two ids, a count of three. Findings are rule-derived and auditable; the prose can still be wrong. Length/structure checks only |

## What an agent framework would have given us

LangGraph / CrewAI / AutoGen were considered and rejected for the core. What they add:
a handoff graph, retries and checkpointing as library features, a standard vocabulary,
and streaming. What they cost here: the orchestrator stops being visible to a reviewer
reading the code, and the tool abstraction — the thing this assignment actually
evaluates — gets buried in framework conventions.

The honest middle ground is wired in already: `ToolRegistry.specs()` returns
OpenAI-shaped function specs, so an LLM could select tools from the same registry the
deterministic pipeline uses. It is **not measured yet**, and this file says so rather
than claiming the framework was unnecessary on the strength of an unmeasured path.

## Two bugs this project found in its own test data and harness

Both are recorded because they changed the numbers, and a reviewer deserves to see
the correction rather than only the corrected state.

1. **Under-labelled ground truth.** Bursts were labelled only by their last
   transaction, so correct detections of the other two were counted as false positives.
   Fixing the labels moved precision 0.148 → 0.305 with no change to the detector.
2. **A dormancy story that was not dormant.** The generator's "dormant reactivation"
   client kept transacting every month, so the 90-day quiet period never existed and
   rule R6 fired **zero** times in 20 clients — an unmeasured rule, not a working one.
   The generator now removes ordinary transactions inside the quiet window, and
   `tests/test_generator.py` asserts the quiet period is real.

The same class of mistake appeared twice more in the harness: the chaos matrix pointed
the client at a port where no server was listening (all 30 runs "degraded" including at
chaos 0.0), and the chaos RNG was re-seeded per request, which turned a "30% failure
rate" into a constant. Both fixed; both facts are in the git history.
