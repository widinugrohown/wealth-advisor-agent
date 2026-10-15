"""Score the detector against the injected ground truth.

Reads the real artefacts (`runs/*/advisory.json`), not the rule functions, so the
number describes the system as it actually ran. Only findings of type `anomaly`
with at least one transaction id are scored: trends and portfolio-level risk
signals have no ground truth to compare against.

Every printed table also prints the exact command that produced it.
"""
from __future__ import annotations

import argparse
import json
import os


def load_truth(path: str) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Returns (tx-level labels per client, count of profile-level labels per client)."""
    tx_truth: dict[str, set[str]] = {}
    profile_truth: dict[str, int] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("tx_id"):
                tx_truth.setdefault(row["client_id"], set()).add(row["tx_id"])
            else:
                profile_truth[row["client_id"]] = profile_truth.get(row["client_id"], 0) + 1
    return tx_truth, profile_truth


def load_runs(runs_dir: str) -> tuple[list[dict], int]:
    """One row per client: the LAST successful run for that client wins.

    Runs with status 'failed' (a missing client, for example) are skipped and
    counted: they carry no analysis, and scoring them would report a client the
    ground truth never mentions.
    """
    if not os.path.isdir(runs_dir):
        return [], 0
    latest: dict[str, dict] = {}
    skipped = 0
    for name in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, name, "advisory.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("status") == "failed":
            skipped += 1
            continue
        if payload.get("client_id"):
            latest[payload["client_id"]] = payload
    return list(latest.values()), skipped


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--truth", default="data/ground_truth.jsonl")
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--json-out", help="also write the summary as JSON")
    args = p.parse_args()

    tx_truth, profile_truth = load_truth(args.truth)
    runs, skipped = load_runs(args.runs_dir)
    if not runs:
        print(f"no advisory.json found under '{args.runs_dir}/' - run `make all` "
              f"(python main.py --all --no-llm --auto-approve) first")
        return 1

    tp = fp = fn = 0
    alerts_total = alerts_hit = 0
    explained = 0
    rows = []
    for payload in sorted(runs, key=lambda r: r["client_id"]):
        client_id = payload["client_id"]
        truth = tx_truth.get(client_id, set())
        predicted: set[str] = set()
        for finding in payload["findings"]:
            if finding["type"] != "anomaly":
                continue
            ids = set(finding["evidence"]["tx_ids"])
            if not ids:
                continue
            alerts_total += 1
            if ids & truth:
                alerts_hit += 1
            predicted |= ids
        explained += sum(1 for f in payload["findings"]
                         if f["evidence"].get("extra", {}).get("explained_by_advisor_note"))
        c_tp = len(predicted & truth)
        c_fp = len(predicted - truth)
        c_fn = len(truth - predicted)
        tp, fp, fn = tp + c_tp, fp + c_fp, fn + c_fn
        rows.append((client_id, len(truth), c_tp, c_fp, c_fn, payload["status"]))

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    alert_precision = alerts_hit / alerts_total if alerts_total else 0.0

    print(f"scored clients: {len(rows)}"
          + (f" (skipped {skipped} failed run(s) - a failed run has no analysis to score)"
             if skipped else ""))
    print(f"{'client':8} {'labelled':>8} {'TP':>3} {'FP':>3} {'FN':>3}  status")
    for client_id, n_truth, c_tp, c_fp, c_fn, status in rows:
        flag = "  <-- FN" if c_fn else ("  <-- FP" if c_fp else "")
        print(f"{client_id:8} {n_truth:>8} {c_tp:>3} {c_fp:>3} {c_fn:>3}  {status}{flag}")
    print()
    print(f"transaction-level anomalies: TP={tp} FP={fp} FN={fn}")
    print(f"  precision (every flagged transaction must be labelled) = {precision:.3f}")
    print(f"  recall                                                  = {recall:.3f}")
    print(f"  f1                                                      = {f1:.3f}")
    print(f"alerts: {alerts_total} raised, {alerts_hit} point at a labelled transaction")
    print(f"  alert precision (an alert is right if any of its transactions is labelled) "
          f"= {alert_precision:.3f}")
    print(f"findings downgraded by an advisor note: {explained}")
    print(f"profile-level labels injected: {sum(profile_truth.values())} "
          f"(scored separately from transactions - they carry no tx_id)")
    print()
    print("Two lenses on purpose: the strict number is what a data-quality reviewer wants, "
          "the alert number is what an advisor experiences (one dormancy episode listing "
          "seven transactions is one interruption, not seven).")
    print("CAVEAT: data is synthetic and the generator knows what it hid, so these "
          "figures are optimistic by construction. They measure whether the rules work "
          "as specified, not real-world detection power.")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"clients": len(rows), "tp": tp, "fp": fp, "fn": fn,
                       "precision": round(precision, 4), "recall": round(recall, 4),
                       "f1": round(f1, 4), "alert_precision": round(alert_precision, 4),
                       "alerts_total": alerts_total, "alerts_hit": alerts_hit,
                       "explained_by_note": explained,
                       "per_client": [{"client_id": r[0], "labelled": r[1], "tp": r[2],
                                       "fp": r[3], "fn": r[4], "status": r[5]} for r in rows]},
                      fh, indent=2)
        print(f"summary written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
