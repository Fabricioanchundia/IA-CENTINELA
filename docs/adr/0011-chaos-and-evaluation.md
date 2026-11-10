# ADR-0011: Chaos runs, recorded evidence and an offline evaluation (Phase 9)

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Until Phase 8 the agent had been judged on a handful of drills, almost all of them "payments scaled to 0". That says little about how good it is, which model to use, or what breaks first. We wanted numbers on many kinds of failure, real ones where possible, and a way to compare models, prompts and guards without breaking the cluster again every time.

## Decision

### 1. Real faults in the cluster: `scripts/chaos.sh`

Ten faults, one after the other, unattended (about 1 h 45 min on the laptop):

| Kind | Faults |
|---|---|
| A component is down | payments, catalog, orders, redis scaled to 0; PostgreSQL scaled to 0 |
| A bad release | payments fails 50% of requests; catalog adds 1.5 s to every request; orders leaks 64 KiB per request |
| Must NOT open an incident | a payments pod deleted (Kubernetes replaces it); an orders rollout to an image that does not exist (the old pod keeps serving) |

The bad releases use **fault-injection switches in the shop** (`shop/chaos.py`): `CHAOS_ERROR_RATIO`, `CHAOS_LATENCY_S`, `CHAOS_LEAK_KB`. They are environment variables read at start-up, so turning one on is a normal rollout, exactly like a bad release. Nothing can switch them over HTTP. Health, readiness and metrics endpoints are never affected, so monitoring has to find the problem. The log messages look like real failures and never say "chaos", so the model cannot read the answer.

Before each fault the script waits until no deployment was rolled out in the last 10 minutes (see 3), so the previous scenario's repair is not mistaken for a cause. Every fault is repaired on exit, even on Ctrl+C. Nobody approves anything during the run: proposals stay pending and expire.

### 2. Record what the model saw, replay it anywhere

Every stored diagnosis now carries a **snapshot of its evidence** (`agent/snapshot.py`), the exact items the model was given. It is useful as an audit trail ("what did the agent know when it proposed this?"), and it turns a chaos incident into a **test case**: `centinela eval snapshot` saves the snapshot plus the known answer to `eval/cases/chaos-*.json`.

`centinela eval run` replays every case through the **production** prompt, validation, retry and proposal guards, on any local model, with only Ollama (no cluster). Fourteen **synthetic cases** (`eval/synthetic.py`) are built with the real `EvidenceBuilder` for what is slow or unsafe to cause on a laptop: two faults at once, a slow or misconfigured database, a crash loop, an OOM kill, a red-herring rollout, a misleading past incident, a blip that already ended. Each case lists the acceptable root causes, categories and proposals. `"none"` means "no automatic action", because the right fix (a rollback, anything on the database, a provider outage) is outside the allowlist.

### 3. The agent asks "what changed?"

Deployments rolled out in the 10 minutes before an incident are flagged `NEW ROLLOUT` in the evidence (from the Deployment's `Progressing` condition; scaling does not count). A recent rollout is a trusted signal for the proposal guards.

## Results

### In the cluster (chaos run, qwen3:8b, 10 November)

| | Result |
|---|---|
| Faults that opened an incident | **8/8** |
| Root cause right, live | **8/8** (the rule-based baseline: 7/8; it blamed catalog when PostgreSQL was down) |
| Right proposal reached the human | 7/8 |
| Time to detect a component down (MTTD) | 74–131 s |
| Time to detect a slow release / a leak | 322 s / 140 s (the latency alert waits 5 minutes on purpose) |
| Detection → AI diagnosis | 66–94 s (60 s of that is the grouping delay) |
| Model time per diagnosis | 4–24 s |
| False-alarm checks | 1 of 2 passed (see "incident 16" below) |

### Offline, 22 cases (8 recorded + 14 synthetic), temperature 0

| | rule-based baseline | qwen2.5:7b | **qwen3:8b** | qwen3:8b + thinking |
|---|---|---|---|---|
| root cause right | 19/22 | 18/22 | **20/22** | 21/22 |
| cause **and** action right | – | 17/22 | **20/22** | 21/22 |
| right action reaches the human | – | 20/22 | **22/22** | 22/22 |
| **wrong action proposed** | – | **2** | **0** | 0 |
| median seconds per case | 0 | 8.2 | **11.3** | 32.8 (max 213) |

Full table, per case: [`docs/eval/results.md`](../eval/results.md).

- **qwen2.5:7b proposed two wrong fixes**: `scale_up catalog` when PostgreSQL was down (a running catalog would not change anything; the policy's "never the database" kept the model from the real target) and `scale_up catalog` for a catalog killed for memory, which would be killed again. qwen3:8b proposed no wrong fix.
- **Thinking helps a little and costs a lot.** It fixed one case (the database password rotated: qwen3 answered `unknown`, with thinking `postgres`), it was 3× slower, and one answer took 213 s. Both qwen3 configurations got every action right.
- **The hardest case is still unsolved:** a slow database (every service slow, none down) was blamed on catalog or `unknown` by all three configurations. "Unknown" with no action is the safe failure: the human still gets the alerts.

## What the chaos run found, and what changed

1. **A late alert caused a false alarm, and inflated an MTTD.** Incident 16 (the "pod deleted" check) was not caused by the deleted pod. A `HighLatencyP95` alert on orders had become active *during* the PostgreSQL outage. That alert averages 5 minutes of requests and then waits 5 more, so it fired 18 s after the PostgreSQL incident had closed and opened a new incident. The same echo made catalog-down start "5 minutes early" (MTTD 323 s instead of 53 s). **Fix:** the tracker now gives a late alert back to the incident it belongs to. If nothing is open and an alert became active while an incident resolved less than 10 minutes ago was still open, that incident is **reopened** (event `incident_reopened`, MTTR extended). Only alerts that are genuinely new open a new incident. Tested with the exact timeline of incident 16 and against PostgreSQL.
2. **A restart for a bad release.** With the payments release failing half its requests, the model blamed payments correctly but proposed `restart payments`, which starts the same broken version again. The past incidents it was shown were all "payments scaled to 0 → scale up", and that history pulled it towards a restart. **Fix (guard in code):** no `restart` is proposed for a deployment rolled out just before the incident; the reason tells the human to consider a rollback. The replay of that case now proposes nothing (`docs/eval/results.md`, "only the v0.8 guards" rows, show the difference).
3. **No more no-op scale-ups** (the Phase 8 red-team finding): `scale_up` is proposed only for a deployment with fewer ready replicas than desired.
4. **The database policy held.** When PostgreSQL was down, qwen3:8b recommended `scale_up postgres`. The policy refused it, as designed in Phase 7: nothing on the database is automated.

## Decision on the model

**qwen3:8b with thinking off stays the default**: the best accuracy per second (20/22, no wrong action, about 11 s). Thinking (21/22) is not worth 3× the latency and a 3-minute worst case in an incident. qwen2.5:7b is faster but proposed wrong fixes. `OLLAMA_THINK=true` remains available.

## Consequences and limits

- Any change to the prompt, the model or the guards can now be measured on 22 cases in about 5 minutes per configuration (15 with thinking), at no cost: `uv run centinela eval run`.
- 22 cases is a small set, 14 of them written by the same people who wrote the agent, and two guards were added after seeing a recorded case (the bad release replay passes because of a rule written for it). The synthetic bad release and the red team are the checks that were not tuned on.
- The reopen rule was verified in tests, not yet in a second chaos run.
- One laptop, one GPU, one run per configuration, temperature 0.
