"""The data generator must be self-consistent.

Every assertion here exists because the corresponding bug really happened while
building this: labels that pointed at ordinary transactions, a "dormant" client
that was never dormant, and ids that could collide after a deletion. A generator
whose stories do not match its own labels makes every later measurement wrong.
"""
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def generate(out_dir: Path, seed: int = 7, clients: int = 5, anomalies: int = 3) -> None:
    subprocess.run([sys.executable, "scripts/generate_clients.py", "--seed", str(seed),
                    "--clients", str(clients), "--anomalies-per-client", str(anomalies),
                    "--out", str(out_dir)], check=True, capture_output=True, cwd=REPO)


def load(out_dir: Path, client_id: str) -> dict:
    return json.loads((out_dir / "clients" / f"{client_id}.json").read_text(encoding="utf-8"))


def truth(out_dir: Path) -> list[dict]:
    return [json.loads(line) for line in
            (out_dir / "ground_truth.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def test_transaction_ids_are_unique(tmp_path):
    generate(tmp_path)
    for client_file in sorted((tmp_path / "clients").glob("*.json")):
        ids = [t["tx_id"] for t in json.loads(client_file.read_text())["transactions"]]
        assert len(ids) == len(set(ids)), f"duplicate id in {client_file.name}"


def test_every_labelled_id_exists_in_the_client_file(tmp_path):
    generate(tmp_path)
    for row in truth(tmp_path):
        if not row["tx_id"]:
            continue
        ids = {t["tx_id"] for t in load(tmp_path, row["client_id"])["transactions"]}
        assert row["tx_id"] in ids, f"{row['tx_id']} is labelled but does not exist"


def test_hard_negatives_are_never_labelled(tmp_path):
    generate(tmp_path)
    labelled: dict[str, set[str]] = {}
    for row in truth(tmp_path):
        if row["tx_id"]:
            labelled.setdefault(row["client_id"], set()).add(row["tx_id"])
    for client_file in sorted((tmp_path / "clients").glob("*.json")):
        data = json.loads(client_file.read_text())
        for tx in data["transactions"]:
            if tx["counterparty"] in ("cp_ins_annual", "cp_family_001"):
                assert tx["tx_id"] not in labelled.get(data["client_id"], set()), (
                    "a legitimate-but-unusual transaction must not be labelled as an anomaly")


def test_a_dormant_story_actually_contains_a_long_quiet_period(tmp_path):
    generate(tmp_path, clients=8, anomalies=4)
    dormant_clients = {r["client_id"] for r in truth(tmp_path)
                       if r["anomaly_type"] == "dormant_reactivation"}
    assert dormant_clients, "seed 7 with 4 anomalies per client should inject at least one dormancy"
    for client_id in dormant_clients:
        dates = sorted(date.fromisoformat(t["date"])
                       for t in load(tmp_path, client_id)["transactions"])
        longest = max((b - a).days for a, b in zip(dates, dates[1:]))
        assert longest > 90, f"{client_id} is labelled dormant but its longest quiet period is {longest} days"


def test_a_burst_is_labelled_transaction_by_transaction(tmp_path):
    generate(tmp_path, clients=8, anomalies=4)
    counts = Counter((r["client_id"], r["anomaly_type"]) for r in truth(tmp_path) if r["tx_id"])
    for (client_id, kind), n in counts.items():
        if kind in ("structuring_burst", "dormant_reactivation"):
            assert n % 3 == 0, f"{client_id}/{kind} labelled {n} transactions, expected a multiple of 3"


def test_same_seed_gives_the_same_data(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    generate(first, seed=11, clients=3)
    generate(second, seed=11, clients=3)
    assert (first / "ground_truth.jsonl").read_text() == (second / "ground_truth.jsonl").read_text()
    for name in sorted(os.listdir(first / "clients")):
        assert (first / "clients" / name).read_text() == (second / "clients" / name).read_text()
