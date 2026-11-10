#!/usr/bin/env bash
# Chaos run (Phase 9): break the shop in 10 real ways, one after the other, and record what
# Centinela did. Unattended: about 2 hours. Keep the laptop awake and plugged in.
#
#   ./scripts/chaos.sh                 every scenario
#   ./scripts/chaos.sh redis-down      only the named scenario(s)
#
# For each fault it waits for an incident and the AI diagnosis, saves EXACTLY what the model saw
# plus the known answer to eval/cases/chaos-<name>.json (an evaluation case that can be replayed
# on any model without the cluster: `centinela eval run`), then repairs the fault and waits for
# the incident to resolve. Two scenarios must NOT open an incident (false-alarm checks).
# Nobody approves anything: the agent's proposals stay pending and expire. Every fault is
# repaired on exit, even on Ctrl+C.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CASES="$ROOT/eval/cases"
LOG="$ROOT/eval/chaos-log.tsv"
SETTLE_MIN=${SETTLE_MIN:-10}   # no rollout this recent before a fault (it would look like a cause)
mkdir -p "$CASES"

K="kubectl -n shop"
cli() { kubectl -n centinela exec deploy/incident-collector -c collector -- centinela "$@"; }
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
latest() { cli incidents list --limit 1 | awk 'NR==2 {print $1, $2}'; }
now() { date +%s; }

# --- faults and their repairs ------------------------------------------------------------
scale() { $K scale "$1" --replicas="$2" >/dev/null; }
chaos_env() { $K set env "deploy/$1" "$2" >/dev/null; }
repair_all() {
  for d in catalog orders payments redis; do scale "deploy/$d" 1; done
  scale statefulset/postgres 1
  for d in catalog orders payments; do
    $K set env "deploy/$d" CHAOS_ERROR_RATIO- CHAOS_LATENCY_S- CHAOS_LEAK_KB- >/dev/null 2>&1 || true
  done
  $K set image deploy/orders orders=centinela-shop:dev >/dev/null
}
trap 'step "Repairing everything"; repair_all' EXIT

# name | expect an incident? | fault command | repair command | root cause(s) | categories |
# right proposals | minutes to wait for the incident | what was broken (for the case file)
SCENARIOS=(
  "payments-down|yes|scale deploy/payments 0|scale deploy/payments 1|payments|service_down|scale_up:payments|8|payments Deployment scaled to 0 replicas"
  "catalog-down|yes|scale deploy/catalog 0|scale deploy/catalog 1|catalog|service_down|scale_up:catalog|8|catalog Deployment scaled to 0 replicas"
  "orders-down|yes|scale deploy/orders 0|scale deploy/orders 1|orders|service_down|scale_up:orders|8|orders Deployment scaled to 0 replicas"
  "redis-down|yes|scale deploy/redis 0|scale deploy/redis 1|redis|cache_failure,dependency_failure,service_down|scale_up:redis|8|redis Deployment scaled to 0 replicas"
  "postgres-down|yes|scale statefulset/postgres 0|scale statefulset/postgres 1|postgres|dependency_failure,service_down,error_spike|none|8|PostgreSQL StatefulSet scaled to 0 (never touched by the agent)"
  "payments-pod-killed|no|$K delete pod -l app.kubernetes.io/name=payments --wait=false|true|-|-|-|6|one payments pod deleted; Kubernetes replaces it in seconds"
  "orders-bad-image|no|$K set image deploy/orders orders=centinela-shop:does-not-exist|$K set image deploy/orders orders=centinela-shop:dev|-|-|-|6|orders rollout to an image that does not exist; the old pod keeps serving"
  "payments-bad-release|yes|chaos_env payments CHAOS_ERROR_RATIO=0.5|chaos_env payments CHAOS_ERROR_RATIO-|payments|error_spike,dependency_failure|none|8|new payments release fails 50% of requests (fix: rollback)"
  "catalog-slow-release|yes|chaos_env catalog CHAOS_LATENCY_S=1.5|chaos_env catalog CHAOS_LATENCY_S-|catalog|high_latency|none|12|new catalog release adds 1.5 s to every request (fix: rollback)"
  "orders-memory-leak|yes|chaos_env orders CHAOS_LEAK_KB=64|chaos_env orders CHAOS_LEAK_KB-|orders|resource_exhaustion|restart:orders,none|14|new orders release leaks 64 KiB per request (fix: rollback; a restart buys time)"
)

wait_no_open_incident() {
  read -r _ status <<<"$(latest) "
  [[ "${status:-}" != "open" ]] && return 0
  note "an incident is still open; waiting for it to resolve"
  for _ in $(seq 90); do
    sleep 10; read -r _ status <<<"$(latest) "
    [[ "$status" != "open" ]] && return 0
  done
  echo "an incident is still open after 15 min: stopping"; exit 1
}

wait_settled() {
  # The agent shows rollouts from the last 10 minutes as suspects. Do not start a fault right
  # after one (k8s.sh up, or the repair of the previous scenario), or it would look like a cause.
  local newest age
  newest=$($K get deploy -o jsonpath='{range .items[*]}{range .status.conditions[?(@.type=="Progressing")]}{.lastUpdateTime}{"\n"}{end}{end}' 2>/dev/null | sort | tail -1) || true
  newest=$(date -d "${newest:-@0}" +%s 2>/dev/null || echo 0)
  age=$(( $(now) - newest ))
  if (( age < SETTLE_MIN * 60 )); then
    note "last rollout $((age / 60)) min ago; waiting $(( (SETTLE_MIN * 60 - age) / 60 + 1 )) min so it is not mistaken for a cause"
    sleep $(( SETTLE_MIN * 60 - age + 5 ))
  fi
}

run() {
  IFS='|' read -r name expect fault repair root category actions wait_min what <<<"$1"
  step "$name: $what"
  wait_no_open_incident
  [[ "$expect" == "yes" ]] && wait_settled
  read -r before _ <<<"$(latest) "; before=${before:-0}
  local started; started=$(now)
  eval "$fault"
  note "fault injected at $(date +%T)"

  local id="" deadline=$(( $(now) + wait_min * 60 ))
  while (( $(now) < deadline )); do
    read -r id _ <<<"$(latest) "
    [[ -n "${id:-}" && "$id" -gt "$before" ]] && break
    id=""; sleep 15
  done

  if [[ "$expect" == "no" ]]; then
    eval "$repair"
    if [[ -z "$id" ]]; then
      note "OK: no incident in $wait_min min (no false alarm)"
      printf '%s\tno-incident\tok\t-\t%s\n' "$name" "$what" >>"$LOG"
    else
      note "FALSE ALARM: incident $id opened"
      printf '%s\tno-incident\tfalse-alarm\t%s\t%s\n' "$name" "$id" "$what" >>"$LOG"
    fi
    return 0
  fi
  if [[ -z "$id" ]]; then
    eval "$repair"
    note "MISSED: no incident after $wait_min min"
    printf '%s\tincident\tmissed\t-\t%s\n' "$name" "$what" >>"$LOG"
    return 0
  fi
  note "incident $id opened $(( $(now) - started )) s after the fault; waiting for the AI diagnosis"

  local file="$CASES/chaos-$name.json" saved=""
  for _ in $(seq 40); do   # up to 10 min: grouping delay + model
    if cli eval snapshot "$id" --name "chaos-$name" --fault "$what" --root "$root" \
         --category "$category" --actions "$actions" >"$file.tmp" 2>/dev/null; then
      mv "$file.tmp" "$file"; saved=yes; break
    fi
    sleep 15
  done
  rm -f "$file.tmp"
  eval "$repair"
  note "repaired at $(date +%T)"
  if [[ -n "$saved" ]]; then
    note "AI diagnosis: $(grep '"summary"' "$file" | tail -1 | sed 's/^ *"summary": //' | cut -c1-140)"
    printf '%s\tincident\tdiagnosed\t%s\t%s\n' "$name" "$id" "$what" >>"$LOG"
  else
    note "no diagnosis in 10 min (check: kubectl -n centinela logs deploy/centinela-agent)"
    printf '%s\tincident\tnot-diagnosed\t%s\t%s\n' "$name" "$id" "$what" >>"$LOG"
  fi
}

wanted=("$@")
step "Chaos run started at $(date +%T). Results: eval/cases/ and eval/chaos-log.tsv"
for scenario in "${SCENARIOS[@]}"; do
  name=${scenario%%|*}
  if (( ${#wanted[@]} )) && [[ ! " ${wanted[*]} " == *" $name "* ]]; then continue; fi
  run "$scenario"
done
step "Chaos run finished at $(date +%T)"
column -t -s $'\t' "$LOG" 2>/dev/null | tail -n 20 || tail -n 20 "$LOG"
