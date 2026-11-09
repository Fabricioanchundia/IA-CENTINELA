# HighErrorRate

**Alert:** more than 5% of requests to a service return 5xx for 2 minutes · **Severity:** critical

## What it means
The service is up but failing requests. In this shop it is almost always a **symptom**:
`orders` returns 503 when `catalog` or `payments` cannot answer.

## Impact
Customers see errors when buying. Every failed order is lost revenue.

## Diagnose
1. Is a dependency down? Check for a simultaneous `ServiceDown` or `UpstreamErrors` alert.
2. Which routes fail:
   `sum by (route, status) (rate(http_requests_total{service="<svc>", status=~"5.."}[5m]))`
3. Error logs (Loki, Grafana → Explore):
   `{project="centinela", service="<svc>", level="error"}` → look at `msg` and `upstream`.
4. Follow one failing request across services with its `request_id`:
   `{project="centinela"} | request_id="<id>"`
5. Database reachable? `curl -s localhost:<port>/readyz` (`database: fail` = PostgreSQL issue).

## Fix
- Dependency down → fix the dependency first ([service-down.md](service-down.md)); this alert
  clears by itself about 2 minutes later (rate window).
- Started right after a deploy → `kubectl -n shop rollout undo deploy/<svc>`.
- Database errors → check `kubectl -n shop get pods -l app.kubernetes.io/name=postgres`.

## Escalate
5xx with all dependencies healthy and no recent deploy: inspect the stack traces in the
`error` field of the logs.
