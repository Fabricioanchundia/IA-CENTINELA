# HighLatencyP95

**Alert:** p95 latency of a service above 500 ms for 5 minutes · **Severity:** warning

## What it means
1 in 20 requests is slow. Usually CPU throttling, a slow database or a slow dependency.

## Diagnose
1. Per route: `histogram_quantile(0.95, sum by (route, le) (rate(http_request_duration_seconds_bucket{service="<svc>"}[5m])))`
2. CPU throttling (container hitting its CPU limit):
   `kubectl -n shop exec deploy/<svc> -- cat /sys/fs/cgroup/cpu.stat` → `nr_throttled / nr_periods`.
   (ADR-0005: Grafana was 98% throttled at 0.5 CPU.)
3. Slow dependency: is `orders` slow while `catalog` or `payments` are slow too?
4. Database: `kubectl -n shop exec statefulset/postgres -- psql -U shop -c "SELECT count(*) FROM pg_stat_activity"`.

## Fix
- Throttled → raise the CPU limit in the manifest and redeploy.
- Slow after a deploy → `kubectl -n shop rollout undo deploy/<svc>`.
- Database saturated → restart the offending client, not the database.
