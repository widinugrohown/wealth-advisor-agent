"""Read runs/*/events.jsonl and print p50/p95 per stage (block B6).

This is where the latency numbers in the README come from - measure, never guess.
"""
from __future__ import annotations

import argparse
import json
import os


def read_events(runs_dir: str) -> list[dict]:
    events: list[dict] = []
    for run in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, run, "events.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return events


def percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
    return values[idx]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("runs_dir", nargs="?", default="runs")
    args = p.parse_args()
    events = read_events(args.runs_dir)
    if not events:
        print("no events yet")
        return 0
    by_agent: dict[str, list[int]] = {}
    for e in events:
        if e.get("event") == "agent_end" and e.get("latency_ms") is not None:
            by_agent.setdefault(e.get("agent", "?"), []).append(int(e["latency_ms"]))
    print(f"{'stage':12} {'n':>4} {'p50 (ms)':>9} {'p95 (ms)':>9}")
    for agent, values in sorted(by_agent.items()):
        print(f"{agent:12} {len(values):>4} {percentile(values, 0.5):>9} {percentile(values, 0.95):>9}")
    degraded = sum(1 for e in events if e.get("event") == "run_end" and e.get("status") == "degraded")
    ended = sum(1 for e in events if e.get("event") == "run_end")
    print(f"runs finished: {ended}, degraded: {degraded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
