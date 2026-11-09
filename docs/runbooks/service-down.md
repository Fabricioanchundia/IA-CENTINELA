# ServiceDown

**Alert:** `up{job="shop"} == 0`, or no `up` series at all, for 1 minute · **Severity:** critical

## What it means
Prometheus has no healthy target for a shop service: the process is dead or unreachable
(`up == 0`), or **no pod exists at all** (scaled to 0, deleted). In Kubernetes the second
case does not produce `up == 0`: the target simply disappears, so the alert also checks for
services seen in the last hour that have no `up` series now.

## Impact
- `payments` down → orders cannot be charged (`orders` returns 503).
- `catalog` down → no prices, orders fail with 503.
- `orders` down → customers cannot buy at all.

## Diagnose
1. Pod state and restarts: `kubectl -n shop get pods -l app.kubernetes.io/name=<service>`
2. Why it restarted: `kubectl -n shop describe pod -l app.kubernetes.io/name=<service>` → look at
   *Last State* (`OOMKilled`, `Error`, exit code) and *Events* (probe failures, image errors).
3. Last logs before the crash: `kubectl -n shop logs deploy/<service> --previous --tail=50`
4. Replicas scaled to 0? `kubectl -n shop get deploy <service>` (READY `0/0`).

## Fix
- Crash loop after a deploy → roll back: `kubectl -n shop rollout undo deploy/<service>`.
- Scaled to 0 → `kubectl -n shop scale deploy/<service> --replicas=1`.
- Stuck pod → `kubectl -n shop rollout restart deploy/<service>`.
- OOMKilled → see [high-memory.md](high-memory.md).

## Escalate
If the pod is `Running` and ready but Prometheus still shows `up == 0`, the problem is
the network or Prometheus itself: check `http://localhost:9090/targets`.
