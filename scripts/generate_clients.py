"""Generate synthetic clients with deliberately injected, labelled anomalies.

Two kinds of unusual activity are written into every client:

  * **labelled anomalies** - recorded in data/ground_truth.jsonl, and
  * **hard negatives** - legitimate but unusual transactions that are NOT labelled,
    which is what keeps the measured precision from being a suspicious 1.00.

The generator writes stories (an oversized transfer to a new counterparty, a
structuring burst, a dormant account waking up); it does not call the detector's
rules. It still knows what it hid, so the precision/recall below is optimistic by
construction - the README says so wherever the number appears.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from datetime import date, timedelta

TODAY = date(2026, 10, 15)
HARD_NEGATIVE_KINDS = ["annual_insurance_premium", "explained_family_transfer"]
ANOMALY_TYPES = ["oversized_new_counterparty", "structuring_burst", "dormant_reactivation",
                 "category_outlier", "liquidity_drain", "concentration"]


def month_starts(end: date, months: int) -> list[date]:
    out, y, m = [], end.year, end.month
    for _ in range(months):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def make_client(rng: random.Random, idx: int, months: int = 12) -> dict:
    client_id = f"c{idx:03d}"
    tx: list[dict] = []
    counter = 0
    base_expense = rng.randint(2_000_000, 9_000_000)
    known_counterparties = [f"cp{n:03d}" for n in rng.sample(range(1, 60), 8)]

    def add(day: date, amount: int, category: str, counterparty: str, note: str = "") -> str:
        nonlocal counter
        counter += 1
        tx_id = f"t{counter:04d}"
        tx.append({"tx_id": tx_id, "date": day.isoformat(), "amount_idr": amount,
                   "category": category, "counterparty": counterparty, "channel": "mobile",
                   "note": note})
        return tx_id

    for start in month_starts(TODAY, months):
        add(start, rng.randint(18_000_000, 30_000_000), "salary", "employer")
        for _ in range(rng.randint(4, 8)):
            add(start + timedelta(days=rng.randint(0, 26)),
                -rng.randint(base_expense // 3, base_expense // 2), "groceries",
                rng.choice(known_counterparties))
        for _ in range(rng.randint(2, 5)):
            add(start + timedelta(days=rng.randint(0, 26)),
                -rng.randint(base_expense // 4, base_expense),
                rng.choice(["dining", "transport", "utilities"]), rng.choice(known_counterparties))

    return {
        "client_id": client_id,
        "name": f"Client {idx:03d}",
        "risk_profile": rng.choice(["conservative", "moderate", "aggressive"]),
        "aum_idr": rng.randint(400_000_000, 9_000_000_000),
        "holdings": [{"asset": asset, "pct": pct} for asset, pct in
                     zip(["equity_fund", "bond_fund", "deposit", "gold"], _pcts(rng))],
        "advisor_notes": [],
        "_tx": tx,
        "_seq": counter,      # monotonic transaction counter: ids are never reused
    }


def _pcts(rng: random.Random) -> list[float]:
    raw = [rng.random() for _ in range(4)]
    total = sum(raw)
    return [round(x / total, 3) for x in raw]


def inject_anomalies(rng: random.Random, client: dict, k: int) -> list[dict]:
    """Add k labelled anomalies. Returns ground-truth rows.

    Two labelling rules, both learned the hard way:

    * **Every** transaction of an injected pattern is labelled, not just the last
      one: an under-labelled ground truth counts a correct detection as a false
      positive.
    * The transaction *before* a quiet period is **not** labelled. It is an
      ordinary transfer; what is anomalous is the burst that follows the silence.
      Labelling it would score a detector down for being right.
    """
    rows: list[dict] = []
    last_day = date.fromisoformat(max(t["date"] for t in client["_tx"]))
    for kind in rng.sample(ANOMALY_TYPES, k=min(k, len(ANOMALY_TYPES))):
        day = last_day - timedelta(days=rng.randint(0, 25))
        if kind == "oversized_new_counterparty":
            tx_ids = [_append(client, day, -rng.randint(40_000_000, 90_000_000),
                              "transfer_out", "cp_new_001")]
        elif kind == "structuring_burst":
            tx_ids = [_append(client, day - timedelta(days=i), -rng.randint(9_100_000, 9_950_000),
                              "transfer_out", "cp_struct_001") for i in range(3)]
        elif kind == "dormant_reactivation":
            # Make the quiet period real: without this the client keeps transacting
            # every month and rule R6 can never fire (measured: 0 hits on 20 clients).
            gap_day = last_day - timedelta(days=rng.randint(120, 200))
            client["_tx"] = [t for t in client["_tx"]
                             if t["note"] == "injected" or not (gap_day < date.fromisoformat(t["date"]) < day)]
            _append(client, gap_day, -rng.randint(4_000_000, 9_000_000), "transfer_out", "cp_dorm_001")
            tx_ids = [_append(client, day - timedelta(days=i), -rng.randint(5_000_000, 12_000_000),
                              "transfer_out", "cp_dorm_001") for i in range(3)]
        elif kind == "category_outlier":
            tx_ids = [_append(client, day, -rng.randint(25_000_000, 45_000_000),
                              "travel", "cp_travel_001")]
        elif kind == "liquidity_drain":
            amount = int(client["aum_idr"] * rng.uniform(0.55, 0.80))
            tx_ids = [_append(client, day, -amount, "transfer_out", "cp_big_001")]
        else:  # concentration: profile-level, no transaction
            client["holdings"][0]["pct"] = 0.88
            rows.append({"client_id": client["client_id"], "tx_id": None, "anomaly_type": kind,
                         "note": "portfolio concentration, no transaction"})
            continue
        rows.extend({"client_id": client["client_id"], "tx_id": tx_id, "anomaly_type": kind}
                    for tx_id in tx_ids)
    return rows


def inject_hard_negatives(rng: random.Random, client: dict) -> None:
    """Legitimate but unusual activity. NOT labelled: a finding here is a false positive."""
    last_day = date.fromisoformat(max(t["date"] for t in client["_tx"]))
    for kind in HARD_NEGATIVE_KINDS:
        day = last_day - timedelta(days=rng.randint(1, 30))
        if kind == "annual_insurance_premium":
            _append(client, day, -rng.randint(15_000_000, 25_000_000), "insurance",
                    "cp_ins_annual", note="hard negative: annual premium")
        else:
            amount = rng.randint(12_000_000, 30_000_000)
            _append(client, day, -amount, "transfer_out", "cp_family_001",
                    note="hard negative: explained to the advisor in advance")
            client["advisor_notes"].append(
                f"{day.isoformat()}: client called ahead about a one-off transfer of "
                f"Rp {amount:,} to cp_family_001 (family house down payment). Expected, "
                f"do not treat as suspicious.")
    client["advisor_notes"].append("2026-08-02: annual portfolio review completed, no change "
                                   "to the risk profile.")
    client["advisor_notes"].append("2026-09-11: client asked about adding a bond fund in Q4.")


def _append(client: dict, day: date, amount: int, category: str, counterparty: str,
            note: str = "injected") -> str:
    """Append one transaction with a never-reused id.

    The id comes from a monotonic counter, not from len(transactions): removing
    ordinary transactions to create a quiet period would otherwise make the next
    id collide with an existing one.
    """
    client["_seq"] = client.get("_seq", len(client["_tx"])) + 1
    tx_id = f"t{client['_seq']:04d}"
    client["_tx"].append({"tx_id": tx_id, "date": day.isoformat(), "amount_idr": amount,
                          "category": category, "counterparty": counterparty,
                          "channel": "mobile", "note": note})
    return tx_id


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--clients", type=int, default=20)
    p.add_argument("--anomalies-per-client", type=int, default=3)
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--no-hard-negatives", action="store_true")
    p.add_argument("--out", default="data")
    args = p.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(os.path.join(args.out, "clients"), exist_ok=True)
    truth: list[dict] = []
    for i in range(1, args.clients + 1):
        client = make_client(rng, i, args.months)
        truth.extend(inject_anomalies(rng, client, args.anomalies_per_client))
        if not args.no_hard_negatives:
            inject_hard_negatives(rng, client)
        client["transactions"] = sorted(client.pop("_tx"), key=lambda t: t["date"])
        client.pop("_seq", None)
        with open(os.path.join(args.out, "clients", f"{client['client_id']}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(client, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "ground_truth.jsonl"), "w", encoding="utf-8") as fh:
        for row in truth:
            fh.write(json.dumps(row) + "\n")

    labelled = sum(1 for r in truth if r["tx_id"])
    print(f"clients: {args.clients} | labelled anomalies: {len(truth)} "
          f"({labelled} transaction-level, {len(truth) - labelled} profile-level) "
          f"| hard negatives: {0 if args.no_hard_negatives else args.clients * len(HARD_NEGATIVE_KINDS)} "
          f"| seed: {args.seed} -> {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
