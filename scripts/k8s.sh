#!/usr/bin/env bash
# Centinela on local Kubernetes (k3d). One command per lifecycle step.
#
#   ./scripts/k8s.sh up       create/update cluster, image, Terraform, workloads
#   ./scripts/k8s.sh status   pods, memory per pod and per node
#   ./scripts/k8s.sh stop     pause the cluster (keeps everything, frees RAM)
#   ./scripts/k8s.sh start    resume a paused cluster
#   ./scripts/k8s.sh console  open the approval console on http://localhost:8400 (Ctrl+C to stop)
#   ./scripts/k8s.sh mcp      print the MCP client configuration (Claude Desktop, VS Code...)
#   ./scripts/k8s.sh delete   remove the cluster and the local Terraform state
set -euo pipefail

CLUSTER=centinela
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TF="terraform -chdir=$ROOT/infra/terraform"
APPS=(catalog payments orders loadgen)

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

up() {
  if ! k3d cluster list "$CLUSTER" >/dev/null 2>&1; then
    step "Creating k3d cluster '$CLUSTER'"
    k3d cluster create --config "$ROOT/deploy/k3d/cluster.yaml"
  else
    k3d cluster start "$CLUSTER" >/dev/null
  fi
  kubectl config use-context "k3d-$CLUSTER" >/dev/null

  step "Building the shop image and importing it into the cluster"
  docker build -q -f "$ROOT/apps/shop/Dockerfile" -t centinela-shop:dev "$ROOT"
  k3d image import centinela-shop:dev -c "$CLUSTER"

  step "Building the Centinela image (collector + AI agent) and importing it"
  docker build -q -t centinela-agent:dev "$ROOT"
  k3d image import centinela-agent:dev -c "$CLUSTER"

  step "Terraform: namespaces, quotas, RBAC and secrets"
  $TF init -input=false -upgrade=false >/dev/null
  $TF apply -input=false -auto-approve

  step "Deploying workloads (kustomize)"
  kubectl apply -k "$ROOT/deploy/k8s"

  # Same image tag after a rebuild: restart so pods pick up the freshly imported image
  for app in "${APPS[@]}"; do kubectl -n shop rollout restart "deployment/$app" >/dev/null; done
  kubectl -n centinela rollout restart deployment/incident-collector deployment/centinela-agent \
    deployment/centinela-executor deployment/centinela-console deployment/centinela-mcp >/dev/null

  step "Waiting for everything to be ready"
  kubectl -n shop rollout status statefulset/postgres --timeout=300s
  for d in redis "${APPS[@]}"; do kubectl -n shop rollout status "deployment/$d" --timeout=300s; done
  for d in prometheus loki alloy grafana; do kubectl -n observability rollout status "deployment/$d" --timeout=300s; done
  kubectl -n centinela rollout status statefulset/incidents-db --timeout=300s
  kubectl -n centinela rollout status deployment/incident-collector --timeout=300s
  kubectl -n centinela rollout status deployment/centinela-agent --timeout=300s
  kubectl -n centinela rollout status deployment/centinela-executor --timeout=300s
  kubectl -n centinela rollout status deployment/centinela-console --timeout=300s
  kubectl -n centinela rollout status deployment/centinela-mcp --timeout=300s

  status
  printf '\nShop: http://localhost:8002/docs   Grafana: http://localhost:3000 (Centinela · Incidents)   Prometheus: http://localhost:9090\n'
}

status() {
  step "Pods"
  kubectl get pods -n shop -o wide --no-headers | awk '{printf "  shop/%-32s %-10s restarts=%s\n", $1, $3, $4}'
  kubectl get pods -n observability --no-headers | awk '{printf "  observability/%-23s %-10s restarts=%s\n", $1, $3, $4}'
  kubectl get pods -n centinela --no-headers 2>/dev/null | awk '{printf "  centinela/%-27s %-10s restarts=%s\n", $1, $3, $4}'
  step "Memory (metrics-server, may take ~1 min after start)"
  kubectl top pods -n shop --no-headers 2>/dev/null | sed 's/^/  shop\//' || echo "  not ready yet"
  kubectl top pods -n observability --no-headers 2>/dev/null | sed 's/^/  observability\//' || true
  kubectl top pods -n centinela --no-headers 2>/dev/null | sed 's/^/  centinela\//' || true
  kubectl top nodes 2>/dev/null || true
}

case "${1:-}" in
  up) up ;;
  status) status ;;
  stop) k3d cluster stop "$CLUSTER" ;;
  start) k3d cluster start "$CLUSTER" && kubectl config use-context "k3d-$CLUSTER" >/dev/null ;;
  console)
    echo "Approval console: http://localhost:8400"
    echo "  user:     $($TF output -raw console_user 2>/dev/null || echo fabricio)"
    # The password is never printed: it goes straight to the Windows clipboard (WSL), so it
    # cannot end up in a screenshot, a terminal log or a chat.
    if command -v clip.exe >/dev/null && $TF output -raw console_password | clip.exe; then
      echo "  password: copied to the clipboard, paste it with Ctrl+V"
    else
      echo "  password: $TF output -raw console_password   (do not share it)"
    fi
    echo "(only this computer can reach it; Ctrl+C to close)"
    exec kubectl -n centinela port-forward --address 127.0.0.1 svc/centinela-console 8400:8000
    ;;
  mcp)
    cat <<'JSON'
Add this server to your MCP client (Claude Desktop: Settings > Developer > Edit Config).
It runs inside the cluster; the client only gets read tools and propose_action.
{
  "mcpServers": {
    "centinela": {
      "command": "wsl.exe",
      "args": ["kubectl", "-n", "centinela", "exec", "-i", "deploy/centinela-mcp", "-c", "mcp",
               "--", "centinela", "mcp"]
    }
  }
}
JSON
    ;;
  delete)
    k3d cluster delete "$CLUSTER"
    rm -f "$ROOT"/infra/terraform/terraform.tfstate*
    ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
