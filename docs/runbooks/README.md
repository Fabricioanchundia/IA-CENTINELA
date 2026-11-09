# Runbooks

One page per alert. Each one follows the same structure, so both humans and the
Centinela agent (Phase 6, via retrieval) can use them:

1. **What it means** · 2. **Impact** · 3. **Diagnose** (exact queries) · 4. **Fix** · 5. **Escalate**

| Alert | Severity | Runbook |
|---|---|---|
| `ServiceDown` | critical | [service-down.md](service-down.md) |
| `HighErrorRate` | critical | [high-error-rate.md](high-error-rate.md) |
| `UpstreamErrors` | warning | [upstream-errors.md](upstream-errors.md) |
| `HighLatencyP95` | warning | [high-latency.md](high-latency.md) |
| `CacheDegraded` | warning | [cache-degraded.md](cache-degraded.md) |
| `HighMemoryUsage` | warning | [high-memory.md](high-memory.md) |

Commands assume Kubernetes (`./scripts/k8s.sh up`). With Docker Compose, replace
`kubectl -n shop logs deploy/<svc>` by `docker compose logs <svc>` and
`kubectl rollout restart` by `docker compose restart <svc>`.

**Golden rule:** alerts often come in groups. Find the **earliest, most upstream** alert
first: `orders` errors are usually a symptom of `payments` or `catalog` being down.
