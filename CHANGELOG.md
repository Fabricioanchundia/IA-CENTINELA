# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.10.0] - 2026-11-10

Every pull request now deploys the whole platform on a fresh Kubernetes cluster and proves it
repairs an outage end to end; images are signed; a static public demo replays real incidents.

### Added
- `e2e` workflow and `scripts/e2e.sh`: k3d on the runner, the real images, Terraform and manifests;
  payments scaled to 0 → incident → AI diagnosis (qwen3:1.7b on CPU) with stored evidence →
  pending fix (agent, or MCP as fallback) → approval → executor → resolved. Summary in the job.
- Cloud drill on AWS (`infra/aws`, `scripts/cloud-drill.sh`): one EC2 instance, no inbound ports,
  no SSH key, no credentials, runs the same end-to-end test on k3s and terminates itself
  (hard limit 2 h; about USD 0.30-0.50 of free-plan credit per run).
- `images` workflow: Trivy scan on every PR; on main, images on ghcr.io with SBOM and provenance,
  signed with Sigstore cosign (keyless).
- `centinela site` and the `pages` workflow: a public demo page in Spanish and English that opens
  with an incident simulator (choose what to break, approve or reject as the on-call engineer,
  try any of the 11 real red-team attacks; everything shown was recorded), then the engineering detail:
  how to run it, architecture, evaluation, red team and every recorded incident. One same-site
  script with no network access (strict CSP); readable without JavaScript.
- ADR-0012.


## [0.9.0] - 2026-11-10

The agent is measured: 10 real faults in the cluster and 22 replayable cases on three model
configurations. The chaos run found three bugs, all fixed.

### Added
- `scripts/chaos.sh`: 10 real faults (5 components down, 3 bad releases, 2 false-alarm checks),
  unattended, every fault repaired on exit.
- Fault-injection switches in the shop (`CHAOS_ERROR_RATIO`, `CHAOS_LATENCY_S`, `CHAOS_LEAK_KB`),
  environment only, never on health or metrics endpoints.
- Evidence snapshots stored with every diagnosis (audit trail, and the basis of replay).
- `centinela eval snapshot` (incident → case), `centinela eval synthesize` (14 synthetic cases),
  `centinela eval run` (replay on local models, writes `docs/eval/results.{md,json}`).
- "What changed?": rollouts in the last 10 minutes are flagged in the evidence.
- ADR-0011 with the live and offline results.

### Changed
- A late alert that became active while a recently resolved incident was still open reopens that
  incident instead of opening a new one (it caused a false alarm and an inflated MTTD).
- No `restart` is proposed for a deployment rolled out just before the incident (roll back).
- No `scale_up` is proposed for a deployment that already has all its replicas.
- Models without a thinking mode (qwen2.5) no longer receive the `think` flag.


## [0.8.0] - 2026-11-10

The agent is attacked with prompt injection on purpose, and guards in code stop what the prompt
alone did not.

### Added
- `centinela redteam`: 13 prompt-injection attacks (logs, Kubernetes events, postmortems) in two
  incidents, against a naive agent, the v0.7 agent and the v0.8 agent, on the local model. Writes
  `docs/redteam/results-<model>-<incident>.{md,json}`. Needs only Ollama.
- Injection detector (`agent/guard.py`): Unicode normalisation, spaced letters, English and
  Spanish override phrases, fake role turns, chat-template tokens, the `<evidence>` delimiter,
  schema field names, fake evidence ids. Suspicious text is quarantined before the prompt.
- Proposal guards: an action needs a trusted signal (alert, measured failure, Kubernetes state)
  on its target; after a quarantine the baseline must agree. The MCP `propose_action` tool applies
  the trusted-signal rule too.
- Warnings for the human: in the proposal reason, in the approval console (red banner) and in the
  MCP `get_incident` response. Metrics `centinela_injection_suspected_total` and
  `centinela_proposals_blocked_total{guard}`.
- ADR-0010 with the measured results: on thin evidence the v0.7 agent let 3 of 11 attacks reach
  the human; the v0.8 agent let none.


## [0.7.0] - 2026-11-10

The agent can now fix things, only with a human's approval: from the terminal, a web console or
another AI assistant over MCP, through a separate executor with the smallest possible permissions.

### Added
- MCP server (`centinela mcp`, Deployment `centinela-mcp`): tools `list_incidents`, `get_incident`,
  `list_actions` (read-only) and `propose_action` (pending only). No approve or execute tool. Own
  database role `centinela_mcp` that can insert proposals but not their status.
  `./scripts/k8s.sh mcp` prints the client configuration; `scripts/mcp-check.py` smoke test.
  ADR-0009.
- The agent loads the model into the GPU as soon as a new incident appears (warm-up), during
  its grouping delay.
- Human-approved actions: after a valid diagnosis the agent proposes `scale_up` or `restart`
  (pending); a human approves or rejects it (`centinela actions | approve | reject`); a separate
  `centinela-executor` runs it and verifies the result.
- Policy in code: allowlist of actions and targets (never PostgreSQL), 1–3 replicas, one open
  action per incident, 15-minute expiry, max 3 executions per hour, re-check before acting,
  `EXECUTOR_DRY_RUN` kill switch.
- `centinela-executor` ServiceAccount (Terraform): `get`/`patch` on four named deployments only.
- Audit trail of every action step in the incident timeline; Grafana "Actions" row.
- `scripts/incident-drill.sh` asks for approval in the terminal.
- Approval console (`centinela console`, `./scripts/k8s.sh console`): web page to approve or reject
  actions, with the AI diagnosis. No Kubernetes rights, own database role `centinela_console`
  (can only record decisions), Terraform-generated password, login lockout, HMAC-signed session,
  CSRF tokens, strict Content-Security-Policy with no JavaScript. `./scripts/k8s.sh console` copies
  the password to the clipboard and never prints it.
- ADR-0008.

### Fixed
- `ServiceDown` is evaluated per service (`max by (service) (up) == 0`), not per pod: a new pod
  still starting during a rollout no longer reports the whole service as down (incident 8).
- The agent no longer proposes an action for an incident that resolved while the model was
  answering (incident 8).
- Rolling updates of the shop no longer cause errors: `preStop` sleep of 5 s on catalog, orders and
  payments. `UpstreamErrors` fires above 0.1 failed calls/s, not on any single error.

### Changed
- `centinela` namespace: up to 8 pods and 1.5 GiB of memory limits.

## [0.5.0] - 2026-11-09

The agent diagnoses incidents by itself with a local model, read-only. First drill: correct root
cause ("payments scaled to 0 replicas") 72 s after detection, 5 s of GPU time.

### Added
- AI diagnosis agent (`centinela agent`, `centinela diagnose ID [--dry-run]`): diagnoses each new
  incident with a local model (Ollama `qwen3:8b` on the GPU) from numbered evidence (alerts,
  metrics, Kubernetes state, grouped logs, runbooks, past incidents), validates that the answer
  cites real evidence, compares it with a rule-based baseline and stores it in the incident.
  Read-only: the suggested action is never executed.
- Deployment `centinela-agent` with the read-only `centinela-agent` ServiceAccount, its own
  `/metrics`, runbooks inside the image.
- Grafana *Centinela · Incidents*: "AI diagnosis" row (valid answers, agreement with the
  baseline, model time, diagnoses table).
- `scripts/incident-drill.sh` shows the automatic diagnosis while the fault is still active.
- Symptoms named by the model without their own alert are removed and recorded (`dropped_symptoms`).
- ADR-0007 with the first measured results.

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
