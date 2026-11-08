# ADR-0004: Observability stack

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Centinela can only diagnose what it can see. It needs metrics (is something wrong, and where?), logs (why?) and alerts (when to wake up). Everything must fit next to the shop on a 16 GB laptop and be switchable on and off.

## Decision

- **Prometheus** for metrics, **Loki** for logs, **Grafana** for dashboards, **Grafana Alloy** to ship container logs. All pinned to exact versions and behind the Compose profile `obs`, so `docker compose up` still starts only the shop.
- **RED metrics in the app** (`prometheus-client`): `http_requests_total` and `http_request_duration_seconds` labelled by method, **route template** and status — never raw paths or IDs, to keep cardinality low. Plus business metrics: orders by status, upstream errors, cache hits/misses/errors. The `service` label comes from the scrape target, not from the app.
- **Memory without cAdvisor:** the client library already exports `process_resident_memory_bytes` per service, enough for the memory-leak scenarios of Phase 9. cAdvisor (~100 MB) is not needed; Kubernetes will provide container metrics in Phase 4.
- **Alert rules live in Prometheus and are unit-tested** with `promtool test rules` in CI: `ServiceDown`, `HighErrorRate`, `HighLatencyP95`, `UpstreamErrors`, `CacheDegraded`, `HighMemoryUsage`.
- **No Alertmanager yet.** Centinela will read firing alerts from the Prometheus API (`/api/v1/alerts`). Routing and notifications are not needed until the agent exists; this saves memory and moving parts.
- **Logs:** Alloy discovers containers of the `centinela` Compose project through the Docker socket (read-only), parses the JSON lines, turns `level` into a label and stores `request_id` as **structured metadata** (searchable without creating one stream per request).
- **Loki single-binary** with local filesystem storage, TSDB schema v13, 72 h retention. **Prometheus** retention 24 h / 512 MB.
- **Grafana** fully provisioned from files (datasources + dashboard), anonymous read-only access, admin password from `.env`, phone-home and plugin pre-install disabled.
- **Traces (OpenTelemetry/Tempo) deferred.** The propagated `X-Request-ID` already correlates a request across services in Loki; tracing can be added later if the agent needs it.
- **A load generator** (`shop.loadgen`, 2 rps by default) keeps realistic traffic flowing, so dashboards and alerts behave like in production.

## Consequences

- Memory caps: Prometheus 256 MB, Loki 256 MB, Grafana 384 MB, Alloy 192 MB, loadgen 96 MB → ~1.2 GB. Grafana started at 256 MB but hit 255/256 MiB on first boot (saturation), so it was raised, plus ~0.7 GB for the shop.
- Validated end to end before merging: killing `payments` fires `ServiceDown` (root cause) together with `HighErrorRate` on orders and `UpstreamErrors` (symptoms); stopping Redis fires `CacheDegraded`. Telling the root cause apart from its symptoms is exactly the agent's job.
- Mounting the Docker socket gives Alloy read access to container metadata. It is read-only and bound to localhost; in Kubernetes (Phase 4) Alloy will read pod logs instead.
