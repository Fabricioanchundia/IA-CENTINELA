#!/usr/bin/env bash
# Incident drill on Kubernetes: break payments on purpose and watch Centinela record it.
#
#   ./scripts/incident-drill.sh          (cluster from ./scripts/k8s.sh up)
#
# 1. scales payments to 0 replicas (the "incident")
# 2. waits until the collector opens an incident (detection -> MTTD)
# 3. keeps payments down for DOWN_S seconds (the AI agent diagnoses it), then brings it back
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

before=$(latest | awk '{print $1}'); before=${before:-0}

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

step "Bringing payments back at $(date +%T)"
restore
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
