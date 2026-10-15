"""The scripts must fail usefully, not crash, when there is nothing to report.

Both scripts are part of the documented workflow (`make eval`, `make latency`).
Running them before the pipeline exists used to raise FileNotFoundError; these
tests pin the behaviour that replaced it.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    """scripts/ is not a package; load a script as a module by path."""
    spec = importlib.util.spec_from_file_location(name, REPO / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, f"scripts/{script}", *args], cwd=REPO,
                          capture_output=True, text=True)


def test_evaluate_says_what_to_run_instead_of_crashing(tmp_path):
    result = run("evaluate_anomaly.py", "--runs-dir", str(tmp_path / "missing"))
    assert result.returncode == 1
    assert "make all" in result.stdout
    assert "Traceback" not in result.stderr


def test_latency_report_says_what_to_run_instead_of_crashing(tmp_path):
    result = run("latency_report.py", str(tmp_path / "missing"))
    assert result.returncode == 1
    assert "make all" in result.stdout
    assert "Traceback" not in result.stderr


def test_latency_report_handles_an_empty_runs_directory(tmp_path):
    (tmp_path / "runs").mkdir()
    result = run("latency_report.py", str(tmp_path / "runs"))
    assert result.returncode == 1
    assert "no events" in result.stdout


def test_failed_runs_are_skipped_and_counted(tmp_path):
    """A failed run (missing client) has no analysis and must not be scored."""
    evaluate = load_module("evaluate_anomaly", "scripts/evaluate_anomaly.py")
    runs = tmp_path / "runs"
    for name, payload in (("20260101T000000-c001-aaaa",
                           {"client_id": "c001", "status": "ok", "findings": []}),
                          ("20260101T000001-tidak-ada-bbbb",
                           {"client_id": "tidak-ada", "status": "failed", "findings": []})):
        (runs / name).mkdir(parents=True)
        (runs / name / "advisory.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded, skipped = evaluate.load_runs(str(runs))
    assert [payload["client_id"] for payload in loaded] == ["c001"]
    assert skipped == 1
