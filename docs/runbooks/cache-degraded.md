# CacheDegraded

**Alert:** catalog cache (Redis) lookups failing for 2 minutes · **Severity:** warning

## What it means
Redis is down or unreachable. `catalog` keeps working by reading PostgreSQL directly
(graceful degradation), so customers are **not** affected yet, but the database gets
more load.

## Diagnose
1. `kubectl -n shop get pods -l app.kubernetes.io/name=redis`
2. `curl -s localhost:8001/readyz` → `"cache": "fail"` confirms it.
3. Database load is rising: `rate(shop_cache_requests_total{result="error"}[5m])`.

## Fix
- `kubectl -n shop rollout restart deploy/redis` (data loss is fine: it is only a cache).

## Escalate
If PostgreSQL latency grows while Redis is down, treat it as [high-latency.md](high-latency.md).
