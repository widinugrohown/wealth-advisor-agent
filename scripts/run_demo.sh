#!/usr/bin/env bash
# End-to-end demo: start the mock CRM, wait for it, run the pipeline, stop the CRM.
#   bash scripts/run_demo.sh c001 --no-llm --auto-approve
#   bash scripts/run_demo.sh --all --no-llm --auto-approve
#   CRM_CHAOS=0.3 bash scripts/run_demo.sh c001 --auto-approve
#   bash scripts/run_demo.sh --chaos-matrix
set -euo pipefail

if [ "${1:-}" = "--chaos-matrix" ]; then
  exec python scripts/run_chaos_matrix.py "${@:2}"
fi

PORT="${CRM_PORT:-8077}"
export CRM_BASE_URL="http://127.0.0.1:${PORT}"

if [ "${1:-}" = "--all" ]; then
  shift
  RUN_ARGS=(--all "$@")
else
  CLIENT="${1:-c001}"
  shift || true
  RUN_ARGS=(--client "${CLIENT}" "$@")
fi

echo "== mock CRM on ${CRM_BASE_URL} (CRM_CHAOS=${CRM_CHAOS:-0.0}) =="
python -m uvicorn mock_crm.server:app --port "${PORT}" --log-level error &
CRM_PID=$!
trap 'kill ${CRM_PID} 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -sf "${CRM_BASE_URL}/health" >/dev/null && break
  sleep 0.5
done
curl -s "${CRM_BASE_URL}/health"; echo

python main.py "${RUN_ARGS[@]}"
