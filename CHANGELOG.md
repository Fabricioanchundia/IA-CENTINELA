# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
