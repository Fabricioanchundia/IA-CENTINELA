#!/usr/bin/env bash
# End-to-end test of the whole loop on a fresh Kubernetes cluster (Phase 10).
# Runs in GitHub Actions on every pull request (.github/workflows/e2e.yml), or locally:
#
#   ./scripts/e2e.sh                       uses the agent's normal model (Ollama on the host)
#   E2E_MODEL=qwen3:1.7b ./scripts/e2e.sh  a small model, as in CI (no GPU there)
#
# break payments -> alert -> incident -> AI diagnosis (evidence stored) -> pending action
# -> a human approves -> the executor scales payments back -> the incident resolves.
# If the (small) model does not propose the fix itself, the fix is proposed through the MCP
# server instead, so the action path is always exercised; the summary says which one it was.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
SUMMARY=${GITHUB_STEP_SUMMARY:-/dev/null}
cli() { kubectl -n centinela exec deploy/incident-collector -c collector -- centinela "$@"; }
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mE2E FAILED: %s\033[0m\n' "$1"; exit 1; }
latest() { cli incidents list --limit 1 | awk 'NR==2 {print $1, $2}'; }
now() { date +%s; }
# poll SECONDS COMMAND...: retry COMMAND every 10 s; non-zero if it never succeeded
poll() {
  local deadline=$(( $(now) + $1 )); shift
  until "$@" >/dev/null 2>&1; do
    (( $(now) < deadline )) || return 1
    sleep 10
  done
}
# wait_for SECONDS DESCRIPTION COMMAND...: like poll, but the test fails on timeout
wait_for() {
  local limit=$1 what=$2; shift 2
  poll "$limit" "$@" || fail "timed out after ${limit}s waiting for: $what"
  printf '    ok: %s\n' "$what"
}

step "Cluster, images, Terraform and workloads"
./scripts/k8s.sh up
if [[ -n "${E2E_MODEL:-}" ]]; then
  # CI: Ollama runs on the runner itself, reachable from the k3d node as host.k3d.internal
  kubectl -n centinela set env deploy/centinela-agent \
    OLLAMA_URL=http://host.k3d.internal:11434 OLLAMA_MODEL="$E2E_MODEL" >/dev/null
  kubectl -n centinela rollout status deploy/centinela-agent --timeout=180s
fi

step "The shop works"
wait_for 180 "orders answers on localhost:8002" curl -fsS http://localhost:8002/readyz
./scripts/smoke.sh

step "Settling (start-up alerts must resolve before the test)"
no_open_incident() { read -r _ status <<<"$(latest) "; [[ "${status:-}" != "open" ]]; }
sleep 90
wait_for 600 "no open incident" no_open_incident
read -r before _ <<<"$(latest) "; before=${before:-0}

step "Fault: payments scaled to 0"
started=$(now)
kubectl -n shop scale deploy/payments --replicas=0
new_incident() { read -r id _ <<<"$(latest) "; [[ -n "${id:-}" && "$id" -gt "$before" ]]; }
wait_for 420 "an incident is opened" new_incident
read -r id _ <<<"$(latest) "
detected=$(( $(now) - started ))
echo "    incident $id opened ${detected}s after the fault"

step "AI diagnosis with its evidence"
snapshot() {
  cli eval snapshot "$id" --name e2e --fault "payments scaled to 0" --root payments \
    --category service_down --actions scale_up:payments >/tmp/e2e-case.json
}
wait_for 600 "the agent stored a diagnosis with evidence" snapshot
read -r root action valid seconds model < <(python3 -c '
import json; lv = json.load(open("/tmp/e2e-case.json"))["live"]
print(lv["root_cause"], lv["action"], lv["valid"], lv["model_seconds"], lv["model"])')
echo "    $model: root cause $root, recommends $action (valid=$valid, ${seconds}s)"

step "A pending action"
pending() { cli actions | awk -v id="$id" '$2 == id && $3 == "pending" {f=1} END {exit !f}'; }
proposer="the AI agent"
if ! poll 60 pending; then
  proposer="an MCP client (the small CI model did not propose it)"
  uv run python scripts/e2e-propose.py "$id"
  wait_for 30 "the MCP proposal is pending" pending
fi
action_id=$(cli actions | awk -v id="$id" '$2 == id && $3 == "pending" {print $1; exit}')
cli actions | awk -v a="$action_id" '$1 == a {print "    " $0}'

step "A human approves; the executor acts"
cli approve "$action_id" --by e2e-ci --note "approved by the end-to-end test"
wait_for 180 "payments is back (1/1 ready)" \
  sh -c '[ "$(kubectl -n shop get deploy payments -o jsonpath={.status.readyReplicas})" = "1" ]'
executed() { cli actions --all | awk -v a="$action_id" '$1 == a && $3 == "executed" {f=1} END {exit !f}'; }
wait_for 180 "action $action_id executed and verified by the executor" executed

step "The incident resolves"
resolved() { read -r _ status <<<"$(latest) "; [[ "${status:-}" == "resolved" ]]; }
wait_for 420 "incident $id resolved" resolved
total=$(( $(now) - started ))
cli incidents show "$id" | sed -n 1,12p # sed reads everything: head would cut the pipe (exit 141)

{
  echo "### End-to-end: payments scaled to 0, fixed through Centinela"
  echo
  echo "| Step | Result |"
  echo "|---|---|"
  echo "| incident opened | ${detected} s after the fault |"
  echo "| AI diagnosis ($model) | root cause **$root**, recommends \`$action\`, valid=$valid, ${seconds} s |"
  echo "| fix proposed by | $proposer |"
  echo "| approved by | e2e-ci (a human in production) |"
  echo "| fault to resolved incident | ${total} s |"
} >>"$SUMMARY"
printf '\n\033[1;32mE2E PASSED\033[0m: fault to resolved incident in %ss\n' "$total"
