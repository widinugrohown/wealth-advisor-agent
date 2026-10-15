.PHONY: install data crm demo demo-no-llm demo-chaos test all eval latency matrix clean

install:
	pip install -r requirements.txt

data:
	python scripts/generate_clients.py --seed 42 --clients 20 --anomalies-per-client 3

crm:
	python -m uvicorn mock_crm.server:app --port 8077

# one client, interactive checkpoint, LLM if a key is present
demo:
	bash scripts/run_demo.sh c001

demo-no-llm:
	bash scripts/run_demo.sh c001 --no-llm --auto-approve

demo-chaos:
	CRM_CHAOS=0.3 bash scripts/run_demo.sh c001 --auto-approve

test:
	pytest -q

# measurements: run every client, then score against the injected ground truth
all:
	bash scripts/run_demo.sh --all --no-llm --auto-approve

# `make all` first: eval scores whatever is in runs/ (it no longer re-runs the
# pipeline, which used to make `make all && make eval` process every client twice)
eval:
	python scripts/evaluate_anomaly.py --json-out runs/eval_summary.json

latency:
	python scripts/latency_report.py runs

matrix:
	python scripts/run_chaos_matrix.py --levels 0,0.3,0.8 --runs-per-level 10

clean:
	rm -rf runs/* runs-chaos memory/*.jsonl __pycache__ .pytest_cache
