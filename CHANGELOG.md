# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-09

Kubernetes platform and incident history: the agent now has a memory.

### Added
- Incident history (`src/centinela/incidents`): collector that turns Prometheus alerts into incidents,
  groups related alerts, attaches Loki error logs as evidence and records MTTD and MTTR in its own
  PostgreSQL (`incidents-db`, namespace `centinela`).
- CLI: `centinela collect` and `centinela incidents list | show | close | stats`.
- Agent image (`Dockerfile`, non-root) deployed as `incident-collector`, with its own `/metrics`.
- Grafana dashboard *Centinela · Incidents* through a `SELECT`-only `grafana_reader` role.
- Runbooks for every alert (`docs/runbooks/`) and `runbook_url` annotations.
- `scripts/incident-drill.sh`: breaks payments on purpose and prints the recorded incident.
- CI: real PostgreSQL service for the store tests and an `agent image` build job.
- ADR-0006.
- Local Kubernetes with k3d (`deploy/k3d/cluster.yaml`) and `scripts/k8s.sh` (up, status, stop, start, delete).
- Terraform (`infra/terraform`): namespaces with Pod Security Standards `restricted`, memory quotas,
  LimitRanges, generated passwords in Secrets, least-privilege RBAC for Prometheus, Alloy and the agent.
- Kustomize manifests for the shop and the observability stack, sharing the Compose configuration.
- Prometheus Kubernetes service discovery and kubelet (cAdvisor) container metrics.
- CI job validating manifests (kubeconform, strict) and Terraform (fmt, validate).
- ADR-0005.
- CI job `compose smoke test`: starts the shop with Docker Compose and runs the end-to-end smoke test.

### Fixed
- Alloy not becoming ready after repeated deploys (memory at 98% of its limit, CPU throttled):
  limits raised to 1 CPU / 384 MiB with `GOMEMLIMIT`, `observability` quota raised to 2 GiB.
- `ServiceDown` now also fires when a service has no scrape target at all (every pod gone, e.g. scaled
  to 0). In Kubernetes `up` disappears instead of becoming 0, so the alert never fired before.
- PostgreSQL pinned back to major 17 after an automatic bump to 18 (incompatible data path and
  data format); Dependabot now ignores PostgreSQL major versions.

## [0.1.0] - 2026-11-08

First release: an observable demo shop, ready to be watched by the agent.

### Added
- Project skeleton: uv, ruff, mypy strict, pytest, pre-commit with gitleaks, CI, Dependabot.
- Demo shop (`apps/shop`): `catalog`, `orders` and `payments` services with PostgreSQL and
  Redis, JSON logs, propagated `X-Request-ID`, `/healthz` and `/readyz`, memory limits.
- Observability (Compose profile `obs`): Prometheus, Loki, Grafana Alloy and Grafana with a
  provisioned dashboard (RED metrics, memory, orders, cache, error logs).
- RED and business metrics on every service (`/metrics`), route-template labels.
- Six alert rules with `promtool` unit tests in CI.
- Load generator for steady background traffic.
- Smoke tests: `scripts/smoke.sh` and `scripts/smoke-obs.sh`.
- ADRs 0001–0004.

### Security
- Database credentials passed as separate libpq variables, never inside connection URLs.
- All ports bound to `127.0.0.1`; services run as a non-root user.
