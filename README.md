# Centinela 🛡️

**An AI SRE agent with a human in charge.** When a microservices shop on Kubernetes breaks, Centinela opens an incident, gathers metrics, logs and cluster state, asks a language model running on local hardware for the root cause, checks every claim against the evidence, and proposes one safe fix. Nothing changes until a person approves it; then a separate component with the smallest possible permissions applies the fix and verifies it. Tested on real faults: **8 of 8 diagnosed correctly, 0 wrong actions in 22 evaluation cases, 0 of 24 prompt-injection attacks got through.**

🌎 **Live demo:** [fabricioanchundia.github.io/IA-CENTINELA](https://fabricioanchundia.github.io/IA-CENTINELA/): an interactive simulator built from real recorded incidents, in Spanish and English.

[![ci](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/ci.yml/badge.svg)](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/ci.yml)
[![e2e](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/e2e.yml/badge.svg)](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/e2e.yml)
[![images](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/images.yml/badge.svg)](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/images.yml)
[![pages](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/pages.yml/badge.svg)](https://github.com/Fabricioanchundia/IA-CENTINELA/actions/workflows/pages.yml)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

> The AI proposes, a person decides. The model never holds credentials and cannot reach the cluster API; the component that can change the cluster cannot think.

🇪🇨 [Resumen en español](#-en-español)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/home-dark.png">
  <img alt="Centinela demo: headline, and the incident simulator with the shop, what Centinela sees and the engineer's phone" src="docs/screenshots/home-light.png">
</picture>

## What it does

| | Feature | How it works |
|---|---|---|
| 🚨 | **Detects incidents**: related alerts become one incident, with time to detect (MTTD) and time to resolve (MTTR) | A collector reads Prometheus alerts every 15 s and keeps the history in its own PostgreSQL; Grafana dashboards |
| 🔎 | **Finds the root cause** and says why, citing numbered evidence | Alerts, per-service metrics, Kubernetes state, error logs, runbooks and similar past incidents go to `qwen3:8b` on the laptop GPU (Ollama). The answer is JSON; citations and the blamed component are validated, and a rule-based baseline gives a second opinion |
| ✋ | **Proposes one safe fix; a person approves it** | Allowlist in code: scale up or restart 4 named services, never the database. Approve from a web console or the CLI; proposals expire after 15 minutes. A separate executor re-checks the policy, acts and verifies |
| 🛡️ | **Resists prompt injection** hidden in logs, events and old postmortems | Suspicious text is quarantined before the model reads it, and an action needs a trusted signal (an alert, a measured failure, Kubernetes state) on its target, never text alone |
| 🔌 | **Works with other AI assistants** (Claude Desktop, IDE agents) | An MCP server with the same power as the agent: read and propose, no approve tool |
| 💥 | **Tested by breaking things on purpose** | `scripts/chaos.sh` causes 10 real faults; each diagnosis stores exactly what the model saw, so every incident becomes a replayable test case |
| ✅ | **Every pull request repairs a real outage** | GitHub Actions creates a fresh Kubernetes cluster, breaks payments and follows the loop to the end: alert, incident, AI diagnosis, approval, repair |
| ☁️ | **Cloud drill on AWS** | Terraform creates one EC2 instance with no open ports, no SSH key and no credentials; it runs the same end-to-end test and terminates itself (about USD 0.30–0.50 per run; first run pending) |
| 📦 | **Signed container images** | Trivy scan; on `main`, images on `ghcr.io` with SBOM and provenance, signed with Sigstore cosign (keyless) |
| 🎮 | **A public simulator anyone can try** | Choose what to break, approve or reject as the on-call engineer, try the real red-team attacks. Static page, no server, no live AI behind it |

| Decide as the on-call engineer | Try to trick the AI |
|---|---|
| <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/decide-dark.png"><img alt="The simulator stops at the proposal: approve or reject" src="docs/screenshots/decide-light.png"></picture> | <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/attack-dark.png"><img alt="A prompt injection in a log line, and what reached the engineer at each defence level" src="docs/screenshots/attack-light.png"></picture> |

The screenshots follow your system's light or dark mode, and so does the demo.

## How good is it? (measured)

**Live, in the cluster** (`scripts/chaos.sh`, 10 faults: five components down including PostgreSQL, three bad releases, two changes that must not raise an alarm):

| | Result |
|---|---|
| Faults that should open an incident, detected and diagnosed | **8/8** |
| Root cause right (rule-based baseline) | **8/8** (7/8) |
| Changes that must not raise an alarm | 2/2 opened no incident of their own (one late alert from the previous fault looked like a false alarm: fixed, below) |
| Time to detect, component down | 74–131 s |
| Detection to AI diagnosis | 66–94 s |
| First approved fix, "approve" to healthy | 15 s |

**Replayed** (`centinela eval run`, 22 cases: 8 recorded + 14 synthetic, through the production prompt, validation and guards):

| 22 cases | rule-based | qwen2.5:7b | **qwen3:8b (in use)** | qwen3:8b + thinking |
|---|---|---|---|---|
| Root cause right | 19 | 18 | **20** | 21 |
| Right action reaches the human | – | 20 | **22** | 22 |
| Wrong action proposed | – | 2 | **0** | 0 |
| Median seconds | instant | 8 | **11** | 33 |

**Prompt-injection red team** (`centinela redteam`, attacks hidden in logs, Kubernetes events and past postmortems; how many put the attacker's action in front of the engineer):

| Incident | No defences | Prompt defences only | **Centinela (guards in code)** |
|---|---|---|---|
| `payments-down` (strong evidence) | 0/13 | 0/13 | **0/13** |
| `slow-orders` (thin evidence, attack repeated 20×) | 2/11 | 3/11 | **0/11** |

What the tests caught, and how each was fixed: a late latency alert that raised a false alarm after an incident had closed (late alerts now reopen their own incident), a restart proposed for a bad release (no restarts right after a rollout: roll back instead), no-op scale-ups (blocked unless the service is under-replicated), and prompt defences that let `restart redis` through on thin evidence (quarantine and the trusted-signal rule). With 22 cases the evaluation compares models and catches regressions; it is not a precise accuracy estimate.

Full results: [`docs/eval/results.md`](docs/eval/results.md) · [`docs/redteam/`](docs/redteam/) · [ADR-0010](docs/adr/0010-prompt-injection-red-team.md) · [ADR-0011](docs/adr/0011-chaos-and-evaluation.md)

## Safety by design

- **Three identities.** The agent proposes (read-only Kubernetes account, no Secrets). The human approves (the console has no Kubernetes rights and a database role that can only record decisions). The executor acts (`get`/`patch` on 4 named deployments). ([ADR-0008](docs/adr/0008-human-approved-actions.md))
- **Allowlist in code, re-checked before acting.** Scale up (1–3 replicas) or restart; never the database, never delete; one open action per incident, max 3 executions per hour, `EXECUTOR_DRY_RUN` kill switch, every step audited.
- **Logs are untrusted.** Customer-controlled text is flattened, fenced, labelled and quarantined when it looks like instructions; the model's answer must cite evidence that exists. ([ADR-0010](docs/adr/0010-prompt-injection-red-team.md))
- **The model runs locally.** Ollama on the laptop GPU: no incident data leaves the machine, and the AI costs $0.
- **Least privilege everywhere.** Pod Security Standards `restricted`, non-root containers, read-only root filesystems, per-component ServiceAccounts, resource quotas; the cloud drill's IAM user can only start one instance type in one region. ([ADR-0005](docs/adr/0005-kubernetes-and-terraform.md), [ADR-0012](docs/adr/0012-ci-cd-and-public-demo.md))
- **A public demo with nothing to attack.** Static page, one same-site script with no network access (CSP `default-src 'none'; script-src 'self'`), every recorded value escaped. The real approval console only listens inside the cluster.
- **No secrets in git.** `.env.example` files, gitleaks in pre-commit and CI, GitHub push protection, Dependabot, protected `main`. ([SECURITY.md](SECURITY.md))

## Architecture

```mermaid
flowchart LR
    subgraph Shop["shop namespace"]
        S1[catalog] --- S2[orders] --- S3[payments]
        DB[(PostgreSQL)]
        R[(Redis)]
    end
    subgraph Obs["observability"]
        P[Prometheus]
        L[Loki]
        G[Grafana]
    end
    subgraph C["centinela namespace"]
        CO[collector] --> AG{{agent<br/>read-only}}
        EX[executor<br/>4 deployments]
        MCP[MCP server]
        CON[approval console]
    end
    Shop -->|metrics| P
    Shop -->|logs| L
    P -->|alerts| CO
    L --> AG
    AG -->|evidence| LLM[local LLM<br/>Ollama, own GPU]
    AG -->|proposal| H((engineer))
    MCP -->|proposal| H
    H -->|approves| CON -->|approval recorded| EX
    EX -->|scale or restart, then verify| Shop
```

Every important decision has an Architecture Decision Record in [`docs/adr/`](docs/adr/):

| ADR | Decision |
|---|---|
| [0002](docs/adr/0002-resource-budget-16gb.md) | Everything runs on a 16 GB laptop with an 8 GB GPU |
| [0004](docs/adr/0004-observability-stack.md) | Prometheus, Loki, Alloy and Grafana, with unit-tested alert rules |
| [0005](docs/adr/0005-kubernetes-and-terraform.md) | k3d + Terraform + Kustomize, restricted Pod Security |
| [0006](docs/adr/0006-incident-history.md) | Alerts grouped into incidents, MTTD/MTTR |
| [0007](docs/adr/0007-ai-diagnosis-agent.md) | A read-only diagnosis agent with a local model |
| [0008](docs/adr/0008-human-approved-actions.md) | Human-approved actions and a separate executor |
| [0009](docs/adr/0009-mcp-server.md) | An MCP server that can read and propose, never act |
| [0010](docs/adr/0010-prompt-injection-red-team.md) | Prompt-injection red team and guards in code |
| [0011](docs/adr/0011-chaos-and-evaluation.md) | Chaos engineering and a replayable evaluation |
| [0012](docs/adr/0012-ci-cd-and-public-demo.md) | End-to-end tests on Kubernetes, cloud drill, signed images, public simulator |

## Tech stack

| Area | Tools |
|---|---|
| Agent and services | Python 3.12, FastAPI, httpx, psycopg 3, uv workspace |
| AI | Ollama (`qwen3:8b`), structured JSON output, evidence validation, rule-based baseline, MCP server, red team, replayable evaluation |
| Platform | Kubernetes (k3d / k3s), Terraform, Kustomize, PostgreSQL, Redis |
| Observability | Prometheus (alert rules tested with `promtool`), Loki, Alloy, Grafana |
| Quality and security | pytest (200+ tests), mypy strict, ruff with security rules, gitleaks, Trivy, cosign, SBOM, Dependabot |
| CI/CD | GitHub Actions (CI, end-to-end on Kubernetes, images, Pages), AWS drill with Terraform |

## Run it yourself

Requirements: Linux, macOS or WSL2 with Docker, [uv](https://docs.astral.sh/uv/), k3d, kubectl, Terraform, and [Ollama](https://ollama.com) for the AI. Designed for 16 GB of RAM.

```bash
git clone https://github.com/Fabricioanchundia/IA-CENTINELA && cd IA-CENTINELA
uv sync --all-packages
ollama pull qwen3:8b                       # the model, about 5 GB

# The whole loop on a fresh cluster: break payments, approve, watch the repair (~20 min)
./scripts/e2e.sh
# No GPU? A small model on the CPU
ollama pull qwen3:1.7b && E2E_MODEL=qwen3:1.7b ./scripts/e2e.sh
```

Day to day:

```bash
./scripts/k8s.sh up            # cluster, images, Terraform, workloads
./scripts/k8s.sh status        # pods and memory per pod
./scripts/incident-drill.sh    # break payments, approve the AI's fix in the terminal
./scripts/k8s.sh console       # approval console on http://localhost:8400
./scripts/chaos.sh             # 10 real faults, about 2 hours, keep the laptop awake
./scripts/k8s.sh stop          # pause the cluster and free RAM

# Without a cluster
uv run centinela eval run      # replay the 22 cases on your model
uv run centinela redteam       # the prompt-injection attacks (~10 min)
uv run centinela site --out _site   # build the public simulator
```

Commands inside the cluster go through the collector, for example `kubectl -n centinela exec deploy/incident-collector -c collector -- centinela incidents list` (also `actions`, `approve ID --by NAME`, `reject ID --by NAME --note WHY`).

| Local URL | What |
|---|---|
| http://localhost:3000 | Grafana: shop overview, incidents with MTTD/MTTR and the AI diagnosis |
| http://localhost:9090 | Prometheus: metrics and firing alerts |
| http://localhost:8001–8003 | The shop: catalog, orders, payments (API docs at `/docs`) |
| http://localhost:8400 | Approval console (`./scripts/k8s.sh console`) |

Only the shop, without Kubernetes: `cp .env.example .env && docker compose --profile obs up -d --build`, then `./scripts/smoke.sh`.

Quality gates: `uv run ruff check . && uv run mypy && uv run pytest`, and pre-commit with secret scanning (`uv run pre-commit install`).

## Project structure

```
src/centinela/        agent (evidence, diagnosis, guards), incidents (collector, history),
                      actions (policy, executor), console, MCP server, red team, evaluation,
                      public simulator (site*.py)
apps/shop/            the demo shop: catalog, orders, payments, load generator, chaos switches
deploy/  infra/       k3d cluster, Kustomize manifests, Terraform (platform and AWS drill)
observability/        Prometheus rules and tests, Loki, Alloy, Grafana dashboards
eval/                 recorded and synthetic incidents, replayable on any model
docs/                 ADRs, runbooks, evaluation and red-team results, screenshots
scripts/              k8s, smoke tests, incident drill, chaos run, end-to-end, cloud drill
tests/                pytest suite (fake LLM, fake Kubernetes, real PostgreSQL in CI)
.github/workflows/    CI, end-to-end on Kubernetes, signed images, Pages
```

## Roadmap

Done:

- ✅ Demo microservices shop with PostgreSQL and Redis, observability with tested alerts → v0.1
- ✅ Local Kubernetes with Terraform, incident history with MTTD/MTTR
- ✅ AI diagnosis with a local model, evidence citations and a rule-based second opinion → v0.5
- ✅ Human-approved actions, a separate least-privilege executor, web console, MCP server → v0.7
- ✅ Prompt-injection red team and guards in code → v0.8
- ✅ Chaos engineering on the live cluster and a replayable evaluation of three model configurations → v0.9
- ✅ End-to-end test on Kubernetes for every pull request, signed images, AWS drill, public simulator → v0.10

Next:

- ⏳ First run of the AWS drill, a short video of a live repair, and v1.0

Known limits: the evaluation has 22 cases, enough to compare models and catch regressions but not a precise accuracy estimate; every configuration misses the slow-database case; the end-to-end test in CI uses a small model on the CPU, so it checks the plumbing while diagnosis quality is measured offline with the production model; the public demo replays recordings and is not live; Centinela runs on a single-node cluster on purpose and is not a production multi-cluster system.

## License

Code: [MIT](LICENSE). Fonts in the demo: Geist (SIL Open Font License).

Built by **Fabricio Anchundia** (Manta, Ecuador).

## 🇪🇨 En español

Centinela es un agente de IA para equipos de operaciones (SRE). Vigila una tienda en línea hecha de microservicios en Kubernetes y, cuando algo se rompe:

- **Detecta** el problema: agrupa las alarmas en un solo incidente y mide cuánto tardó en notarse y en arreglarse.
- **Encuentra la causa**: junta métricas, registros, el estado de Kubernetes, manuales e incidentes pasados, y un modelo de IA que corre en la computadora propia (sin enviar datos a nadie y sin costo) explica la causa citando las pruebas.
- **Propone un arreglo seguro, y una persona decide**: la IA nunca cambia nada por su cuenta. Un componente aparte, con los permisos mínimos, aplica el arreglo solo después de la aprobación y comprueba que funcionó.
- **No se deja engañar**: cualquiera puede escribir instrucciones en los registros (por ejemplo, en un cupón). Centinela las pone en cuarentena y no actúa sobre un servicio sin una señal confiable.

**Probado de verdad:** se provocaron 10 fallas reales en el sistema; las 8 que debían abrir un incidente se detectaron y se diagnosticaron bien. En 22 casos de evaluación no propuso ninguna acción equivocada, y de 24 ataques para engañarla, ninguno logró su objetivo. Cada pull request levanta un Kubernetes nuevo, rompe un servicio y comprueba que se repara de punta a punta.

**Pruébalo:** [fabricioanchundia.github.io/IA-CENTINELA](https://fabricioanchundia.github.io/IA-CENTINELA/) es un simulador en español e inglés: eliges qué romper, decides como ingeniero de guardia (aprobar o rechazar) e intentas engañar a la IA con los ataques reales que se probaron. Para usar la IA real, sigue los pasos de [Run it yourself](#run-it-yourself) en tu máquina.

👉 [Abrir el simulador](https://fabricioanchundia.github.io/IA-CENTINELA/)
