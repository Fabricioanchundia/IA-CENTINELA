# ADR-0003: Demo shop design

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Centinela needs a realistic "patient": several services that depend on each other, a database and a cache, so that failures cascade the way they do in production. It must stay small enough to run next to the observability stack and Kubernetes on a 16 GB laptop.

## Decision

- **Three services** with a real dependency chain: `orders` → `catalog` (price) and `orders` → `payments` (charge). A failure in catalog or payments shows up as a 503 in orders, which is the kind of root-cause puzzle the agent must solve.
- **Python + FastAPI**, sync endpoints, `psycopg` 3 with a pool of max 4 connections, `httpx` with a 2 s timeout for service-to-service calls.
- **One Docker image, three commands.** The services live in one package (`apps/shop`, a uv workspace member) and the image runs `uvicorn shop.<service>:app`. One build instead of three, ~one image layer set on disk.
- **One PostgreSQL instance, one table per service.** In a real company each service would own its database; here a shared instance saves ~250 MB. Services never read each other's tables.
- **Redis as a cache only** (catalog product list, 30 s TTL). If Redis fails, catalog falls back to PostgreSQL and `/readyz` reports `cache: fail` — graceful degradation, a useful scenario for the agent.
- **Observability-ready from day one:** JSON logs on stdout (one object per line), an `X-Request-ID` propagated across services, `/healthz` (liveness) and `/readyz` (readiness, checks dependencies).
- **Hard limits in Compose:** PostgreSQL 256 MB, Redis 64 MB (`maxmemory 48mb`, LRU), each service 128 MB and 0.5 CPU. Total cap ≈ 700 MB.
- **Ports bound to 127.0.0.1** only; PostgreSQL and Redis are not exposed to the host at all.

## Consequences

- The whole shop fits in well under 1 GB, leaving room for Prometheus, Grafana and Loki in Phase 3.
- Repositories are hidden behind small protocols with in-memory fakes, so unit tests run in ~1 s without Docker.
- Shared database and no message queue keep it simple; both are documented trade-offs, not oversights.
- **Lesson: database majors are migrations.** Dependabot proposed `postgres` 17 → 18 (compatibility score 64%) and the PR merged because CI only validated the Compose *syntax*. PostgreSQL 18 images store data in `/var/lib/postgresql/18/docker` and refuse to start with the old mount, and a new major cannot read old data files anyway. Fix: pin major 17, tell Dependabot to ignore `postgres` major updates, and add a CI job that actually starts the stack and runs `scripts/smoke.sh`, so a broken image can no longer reach `main`.
