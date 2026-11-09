#!/usr/bin/env bash
# Checks the observability stack.
# Usage: ./scripts/smoke-obs.sh   (with: docker compose --profile obs up -d)
set -euo pipefail

PROM=${PROM:-http://localhost:9090}
LOKI=${LOKI:-http://localhost:3100}
GRAFANA=${GRAFANA:-http://localhost:3000}

pass() { printf '  \033[32m✔\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✘\033[0m %s\n' "$1"; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

# Wait up to 60 s for a command to succeed (logs and first scrapes need a few seconds)
retry() { for _ in $(seq 1 30); do "$@" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }

echo "1) Prometheus"
retry curl -fsS "$PROM/-/ready" && pass "ready" || fail "not ready"
up_count() { curl -fsS "$PROM/api/v1/query" --data-urlencode 'query=count(count by (service) (up{job="shop"} == 1))' \
  | json 'int(float(d["data"]["result"][0]["value"][1])) if d["data"]["result"] else 0'; }
retry test "$(up_count)" -eq 3 && pass "3/3 shop services scraped" || fail "only $(up_count)/3 shop services up"
rules=$(curl -fsS "$PROM/api/v1/rules" | json 'sum(len(g["rules"]) for g in d["data"]["groups"])')
[ "$rules" -ge 6 ] && pass "$rules alert rules loaded" || fail "alert rules missing"

echo "2) Loki"
retry curl -fsS "$LOKI/ready" && pass "ready" || fail "not ready"
has_logs() { curl -fsS "$LOKI/loki/api/v1/label/service/values" | json '"orders" in d.get("data", [])' | grep -q True; }
retry has_logs && pass "receiving shop logs (via Alloy)" || fail "no shop logs in Loki"

echo "3) Grafana"
retry curl -fsS "$GRAFANA/api/health" && pass "healthy" || fail "not healthy"
title=$(curl -fsS "$GRAFANA/api/dashboards/uid/centinela-shop" | json 'd["dashboard"]["title"]')
pass "dashboard: $title"

echo "4) Firing alerts"
curl -fsS "$PROM/api/v1/alerts" | python3 -c '
import json, sys
firing = [a for a in json.load(sys.stdin)["data"]["alerts"] if a["state"] == "firing"]
for a in firing:
    print("  ! " + a["labels"]["alertname"] + ": " + a["annotations"]["summary"])
if not firing:
    print("  none, all quiet")'

echo "All good ✅  Dashboard: $GRAFANA/d/centinela-shop"
