# HighMemoryUsage

**Alert:** a service uses more than 100 MiB (limit 128 MiB) for 2 minutes · **Severity:** warning

## What it means
The process resident memory (RSS) is close to the container limit. If it keeps growing
it will be **OOMKilled** and restarted, which then fires `ServiceDown`.

## Diagnose
1. Trend: `process_resident_memory_bytes{service="<svc>"}` in Grafana. Steady growth = leak;
   a plateau = the limit is just too tight.
2. Already killed? `kubectl -n shop describe pod -l app.kubernetes.io/name=<svc>` →
   *Last State: Terminated, Reason: OOMKilled*.
3. Real memory vs page cache (ADR-0004): `kubectl -n shop exec deploy/<svc> -- cat /sys/fs/cgroup/memory.stat`
   → `anon` is real memory, `file` is reclaimable cache.

## Fix
- Leak after a deploy → `kubectl -n shop rollout undo deploy/<svc>`.
- Immediate relief → `kubectl -n shop rollout restart deploy/<svc>` (buys time, does not fix a leak).
- Limit too tight for normal load → raise `resources.limits.memory` within the namespace quota.
