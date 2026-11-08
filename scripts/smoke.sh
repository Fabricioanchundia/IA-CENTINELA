#!/usr/bin/env bash
# End-to-end smoke test of the demo shop.
# Usage: ./scripts/smoke.sh   (with the stack running: docker compose up -d)
set -euo pipefail

CATALOG=${CATALOG:-http://localhost:8001}
ORDERS=${ORDERS:-http://localhost:8002}
PAYMENTS=${PAYMENTS:-http://localhost:8003}

pass() { printf '  \033[32m✔\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✘\033[0m %s\n' "$1"; exit 1; }

echo "1) Health"
for url in "$CATALOG" "$ORDERS" "$PAYMENTS"; do
  curl -fsS "$url/readyz" >/dev/null && pass "$url is ready" || fail "$url is not ready"
done

echo "2) Catalog"
count=$(curl -fsS "$CATALOG/products" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "$count" -ge 1 ] && pass "$count products listed" || fail "no products"

echo "3) Place an order (2 x product 1)"
order=$(curl -fsS -X POST "$ORDERS/orders" -H 'Content-Type: application/json' \
  -H 'X-Request-ID: smoke-test-1' -d '{"product_id": 1, "quantity": 2}')
status=$(echo "$order" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
id=$(echo "$order" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
[ "$status" = "paid" ] && pass "order $id is paid" || fail "order status: $status"

echo "4) Read it back"
curl -fsS "$ORDERS/orders/$id" >/dev/null && pass "order $id found" || fail "order not found"

echo "All good ✅"
