# UpstreamErrors

**Alert:** `orders` fails more than 0.1 calls/s to a dependency for 1 minute · **Severity:** warning

## What it means
`orders` cannot reach `catalog` or `payments` (label `upstream`): connection refused,
timeout (2 s) or a non-200 answer. It points directly at the dependency to investigate.

## Diagnose
1. Which upstream: label `upstream` of the alert.
2. Is it up? `kubectl -n shop get pods -l app.kubernetes.io/name=<upstream>`
3. Error type in `orders` logs: `{project="centinela", service="orders"} |= "unavailable"`
   - `ConnectError` → upstream down or not ready.
   - `ReadTimeout` → upstream slow: check [high-latency.md](high-latency.md).

## Fix
Fix the upstream service ([service-down.md](service-down.md)). Restarting `orders` does
**not** help: it is the messenger, not the cause.
