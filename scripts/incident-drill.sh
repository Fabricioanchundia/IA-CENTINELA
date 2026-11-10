#!/usr/bin/env bash
# Incident drill on Kubernetes: break payments on purpose and watch Centinela record it.
#
#   ./scripts/incident-drill.sh          (cluster from ./scripts/k8s.sh up)
#
# 1. scales payments to 0 replicas (the "incident")
# 2. waits until the collector opens an incident (detection -> MTTD)
# 3. keeps payments down for DOWN_S seconds while the AI agent diagnoses it and proposes a fix
#    -> you approve it (the executor scales payments back) or reject it (restored by hand)
# 4. waits until every alert clears and the incident is resolved (-> MTTR)
# 5. prints the incident timeline: alerts, logs evidence, resolution
set -euo pipefail

DOWN_S=${DOWN_S:-180}
cli() { kubectl -n centinela exec deploy/incident-collector -c collector -- centinela "$@"; }
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
# newest incident as "id status" (empty if none yet)
latest() { cli incidents list --limit 1 | awk 'NR==2 {print $1, $2}'; }

restore() { kubectl -n shop scale deploy/payments --replicas=1 >/dev/null; }
trap restore EXIT   # payments always comes back, even on Ctrl+C

read -r before before_status <<<"$(latest) "
before=${before:-0}
if [[ "${before_status:-}" == "open" ]]; then
  # A new fault would join the open incident (grouping, ADR-0006) instead of opening a new one
  step "Incident $before is still open (its alerts are clearing). Waiting for it to close first"
  for _ in $(seq 60); do
    read -r _ before_status <<<"$(latest) "
    [[ "$before_status" != "open" ]] && break
    sleep 10; printf '.'
  done
  echo
  [[ "$before_status" != "open" ]] || { echo "incident $before still open after 10 min"; exit 1; }
fi

step "Breaking payments (replicas=0) at $(date +%T)"
kubectl -n shop scale deploy/payments --replicas=0

step "Waiting for Centinela to open an incident (alerts need ~1-2 min)"
for _ in $(seq 60); do
  read -r id status <<<"$(latest) "
  if [[ -n "${id:-}" && "$id" -gt "$before" ]]; then break; fi
  sleep 10; printf '.'
done
[[ -n "${id:-}" && "$id" -gt "$before" ]] || { echo "no incident after 10 min"; exit 1; }
echo; echo "  incident $id opened at $(date +%T)"

step "Keeping payments down for ${DOWN_S}s (alerts join the incident, the AI agent diagnoses it)"
sleep "$DOWN_S"

step "AI diagnosis, made automatically while payments was still down"
if cli incidents show "$id" | grep -q "diagnosis"; then
  cli incidents show "$id" | sed -n '/diagnosis$/,/baseline/p'
else
  echo "  no diagnosis yet. Check: kubectl -n centinela logs deploy/centinela-agent --tail=20"
fi

step "Action proposed by the agent, waiting for a human"
action=$(cli actions | awk -v id="$id" '$2 == id && $3 == "pending" {print $1; exit}')
fixed_by_agent=0
if [[ -n "$action" ]]; then
  cli actions
  answer=n
  if [[ -t 0 ]]; then
    # The proposal expires 15 min after it was made: do not wait longer than ~14 min here
    printf '\a\n  \033[1;33mYOUR DECISION IS NEEDED: the drill waits up to 14 min (then the action expires).\033[0m\n'
    read -r -t 840 -p "  Approve action $action? [y/N] " answer || answer=n
  fi
  if [[ "$answer" =~ ^[yYsS] ]] &&
    cli approve "$action" --by "${APPROVER:-$USER}" --note "approved during the incident drill"; then
    step "The executor runs the approved action and verifies it"
    for _ in $(seq 40); do
      state=$(cli actions --all | awk -v a="$action" '$1 == a {print $3; exit}')
      [[ "$state" != "approved" && "$state" != "pending" ]] && break
      sleep 3; printf '.'
    done
    echo
    cli actions --all --limit 1
    [[ "$state" == "executed" ]] && fixed_by_agent=1
  else
    cli reject "$action" --by "${APPROVER:-$USER}" --note "not approved during the drill: fixed by hand" ||
      echo "  (the action was already closed)"
  fi
else
  echo "  no action proposed (check: kubectl -n centinela logs deploy/centinela-agent --tail=20)"
fi

if [[ "$fixed_by_agent" == 1 ]]; then
  step "payments was restored by the executor at $(date +%T), after human approval"
else
  step "Bringing payments back by hand at $(date +%T)"
  restore
fi
kubectl -n shop rollout status deploy/payments --timeout=120s

step "Waiting for every alert to clear (error-rate windows need a few minutes)"
for _ in $(seq 60); do
  status=$(cli incidents list --limit 20 | awk -v id="$id" '$1 == id {print $2}')
  [[ "$status" == "resolved" ]] && break
  sleep 10; printf '.'
done
echo
[[ "$status" == "resolved" ]] || { echo "incident $id still open after 10 min"; exit 1; }

step "Incident $id"
cli incidents show "$id"
printf '\nWrite the postmortem with:\n  kubectl -n centinela exec deploy/incident-collector -c collector -- \\\n    centinela incidents close %s --cause "..." --resolution "..."\n' "$id"
printf 'Dashboard: http://localhost:3000/d/centinela-incidents\n'
