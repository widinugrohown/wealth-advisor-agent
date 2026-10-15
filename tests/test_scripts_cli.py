"""The scripts must fail usefully, not crash, when there is nothing to report.

Both scripts are part of the documented workflow (`make eval`, `make latency`).
Running them before the pipeline exists used to raise FileNotFoundError; these
tests pin the behaviour that replaced it.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


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
