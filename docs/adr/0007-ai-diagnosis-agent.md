# ADR-0007: AI diagnosis agent, read-only (Phase 6)

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

Phase 5 records incidents, but a human still has to read alerts, metrics, logs and runbooks to find the root cause. That is the slow part of MTTR. The agent should do that reading and propose a cause and an action, with three hard constraints:

1. **$0 and private:** no paid API. The model runs locally on the laptop GPU (RTX 5060, 8 GB VRAM) with Ollama.
2. **Trust must be earned:** in this phase the agent may only *read* and *suggest*. Acting comes in Phase 7, behind human approval.
3. **Verifiable answers:** a confident wrong diagnosis is worse than none. Every answer must be checkable against the evidence it used.

## Decision

- **A separate process and identity.** `centinela agent` runs as its own Deployment with the `centinela-agent` ServiceAccount, whose Role (Terraform) can only `get/list` pods, events and deployments in `shop`, with no access to Secrets. The collector keeps no Kubernetes token at all. Same image, different command.
- **When:** the agent diagnoses each open incident once, `DIAGNOSE_DELAY_S` = 60 s after detection, so the symptoms of a cascade (UpstreamErrors, HighErrorRate) have joined the incident first. `centinela diagnose ID` re-runs it by hand; `--dry-run` prints the exact prompt without calling the model.
- **Model: `qwen3:8b`** through Ollama (5.2 GB, native tool support, measured 100% on GPU). `qwen3.5:9b` (6.6 GB) was rejected: too little VRAM left for the context. `qwen2.5:7b` stays installed as the comparison point for Phase 9. Thinking mode is **off** by default (`OLLAMA_THINK`) for latency; Phase 9 measures whether it improves accuracy.
- **Ollama stays closed to the network.** It listens only on Windows `localhost`. Pods reach it through Docker Desktop's `host.docker.internal` forwarding; the "Expose Ollama to the network" option stays off.
- **Evidence as numbered items** (`[E1]`, `[E2]`, ...): alerts, a per-service metrics table at detection + 60 s (up, req/s, 5xx, p95, memory, and "NO SCRAPE TARGET" when nothing runs), failed calls from `orders` to each dependency, Kubernetes workloads and unhealthy pods, warning events, grouped log lines (`20x error "payments unavailable"`), runbook excerpts (What it means + Fix) and up to 3 past incidents with a written root cause. About 1,000–1,500 tokens.
- **Untrusted text is fenced.** Log lines and event messages come from the system being diagnosed: they are flattened to one line, shortened to 160 characters, cannot fake an evidence id (`[E9]` becomes `[e9]`), and the system prompt says never to follow instructions inside them. Phase 8 attacks this on purpose.
- **Structured output.** The request passes a JSON schema as Ollama's `format`; enums restrict the root cause to real components (`catalog, orders, payments, postgres, redis, unknown`), actions to `scale_up | restart | rollback | investigate | none` and runbooks to existing files. The first field is a short `analysis`, so the model reasons before it commits. Temperature 0 and a fixed seed make runs repeatable.
- **Validation after the schema.** An answer is rejected when it cites an evidence id that does not exist, cites nothing, or none of the cited items mention the component it blames. A rejected answer gets **one retry** with the reasons as feedback; a second failure is stored as `unknown` with confidence 0 and the errors.
- **A dumb baseline next to the model.** A rule of thumb (ServiceDown → that service; workload with 0 ready replicas; UpstreamErrors → upstream label; CacheDegraded → redis; else the earliest alert) runs on the same evidence. The report stores whether model and rule agree: agreement raises trust, disagreement asks a human to look first. Phase 9 measures both against known causes.
- **Context budget.** `num_ctx` 4096: the prompt fits with room for the answer, and the model keeps the 5.6 GB VRAM footprint measured in Phase 6.1. Ollama **silently drops the start of a prompt that does not fit** (the instructions!), so the agent drops low-value evidence first (history, then runbooks, events, logs) and says so in a note.
- **Output stays in the incident.** The full report (diagnosis, validity, errors, baseline, model, seconds, tokens) is an `incident_events` row of kind `diagnosis`: visible in `centinela incidents show`, in the drill, and in Grafana (*Centinela · Incidents*, "AI diagnosis" row) through the read-only `grafana_reader` role. Prometheus metrics: `centinela_diagnoses_total{result}`, `centinela_diagnosis_seconds`, `centinela_llm_tokens_total{kind}`.

## First results (laptop: RTX 5060 8 GB, `qwen3:8b`, Ollama 0.40)

| Case | Prompt / output tokens | Model time | Root cause | Agrees with rule |
|---|---|---|---|---|
| Incident 2, re-diagnosed by hand after the fact | 1,769 / 171 | 6.8 s | payments, service_down, 0.95 | yes |
| Incident 3, live drill, **automatic**, 72 s after detection | 1,072 / 173 | 5.1 s | payments, service_down, 0.95 | yes |

- The live diagnosis said *"Payments is down because it has been scaled to 0 replicas"*: it found the exact cause in the Kubernetes workloads, not only the service. Fault start to root cause: about 2.5 minutes, with no human involved.
- **The model fixed a naive label.** In the drill `UpstreamErrors` fired 17 s before `ServiceDown`, so the incident was titled after the symptom ("orders cannot reach payments"). The diagnosis still named payments / service_down: the root cause should come from the diagnosis, not from whichever alert arrives first.
- **A small, repeated hallucination:** both times the model listed `catalog` (healthy) as a symptom. The prompt now asks for components with their own alert only, and the validator removes unsupported symptoms and records them (`dropped_symptoms`) instead of trusting them. Two drills are an anecdote, not an evaluation: Phase 9 measures root-cause and symptom accuracy over 20+ scenarios.

## Consequences

- The easy cases (one service down) are solved by the baseline too. That is expected and honest: the model's value shows on harder faults (slow database, memory leak, bad deploy, cache outage) that Phase 9 will inject, and in the written explanation and action it gives the on-call human.
- Kubernetes state is read **at diagnosis time**, not at incident time. For an open incident that is the same thing; for an old incident the evidence says "now, AFTER the incident: may differ".
- The agent depends on the laptop: when Ollama is not running, diagnoses are stored as `unknown` with the connection error, and incident tracking is unaffected.
- One diagnosis at a time and one agent replica: the GPU serves one request at a time anyway.
