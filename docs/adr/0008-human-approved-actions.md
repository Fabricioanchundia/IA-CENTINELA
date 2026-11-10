# ADR-0008: Human-approved actions (Phase 7)

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

Phase 6 diagnoses incidents and *suggests* a fix (for example `scale_up payments`). A human still has to type the `kubectl` command, so MTTR still waits on someone reading the diagnosis, opening a terminal and remembering the command. We want the agent to fix things, without giving a language model the power to change production on its own: models hallucinate (Phase 6 already caught one), and Phase 8 will try to manipulate it through logs on purpose.

## Decision

**Propose → approve → execute, done by three different identities.**

| Step | Who | Kubernetes permissions |
|---|---|---|
| Propose | `centinela-agent` (after a valid diagnosis) | read-only on `shop` (Phase 6) |
| Approve / reject | a **human**, with `centinela approve` / `reject`, later the web page (7b) | **none**: it only writes a decision to the database |
| Execute | `centinela-executor` | `get` + `patch` on `deployments` and `deployments/scale`, restricted with `resourceNames` to `catalog, orders, payments, redis` |

A compromised web page or a manipulated model can, at most, create or approve a request. Only the executor can change the cluster, it is not reachable over the network, and it re-checks every rule before acting.

**The rules live in code (`actions/policy.py`), not in the prompt:**

- **Allowlist:** `scale_up` (1–3 replicas) and `restart` (rolling restart) only. No delete, no create, no exec. `rollback` is left out until there is a safe way to pick the revision.
- **Never the database:** PostgreSQL is not a target. Restarting a database is a human decision with a plan.
- **Only a human approves:** names like `agent` or `centinela-agent` are refused, the approver's name is recorded, and rejecting needs a reason (it goes into the postmortem).
- **One open action per incident** (a partial unique index in PostgreSQL).
- **Expiry:** a pending action expires after 15 minutes. An approval must be about the current situation, not one from an hour ago.
- **Rate limit:** at most 3 executed actions per hour, to cap the blast radius of a bad loop.
- **Re-check before acting:** the executor applies the policy again and skips actions that are no longer needed (`payments already has 1/1`).
- **Verify after acting:** the action is `executed` only when the deployment has its replicas ready again (within 90 s); otherwise it is `failed`, with the reason.
- **Kill switch:** `EXECUTOR_DRY_RUN=true` records what would happen and changes nothing.

**Every state change is one atomic UPDATE guarded by the current state** (`... WHERE status = 'pending' AND expires_at > now`), so two people, or the CLI and the web page, cannot both decide the same action. Each change is also written to `incident_events`: the incident timeline is the audit trail (who proposed, who approved and when, what happened). Grafana shows it in the "Actions" row through the read-only role.

## First approved fix (drill, incident 7)

| Step | Time (UTC) |
|---|---|
| payments scaled to 0 | 00:17:23 |
| incident opened (MTTD 68 s) | 00:19:09 |
| AI diagnosis "payments scaled to 0 replicas", 17 s of GPU; action proposed | 00:20:31 |
| human approves in the terminal (the drill keeps the fault for 3 minutes first) | 00:23:14 |
| executor: `0/0 -> 1/1 ready after 9 s`, action `executed` | 00:23:29 |
| `ServiceDown` cleared | 00:23:36 |

From approval to a healthy service: **15 seconds**, with nobody typing a `kubectl` command. Without the drill's deliberate 3-minute wait, fault-to-fix would be about 3.5 minutes, most of it alert `for:` windows and the agent's 60-second grouping delay.

## Lessons from the first drills

- **The expiry works, and humans are slow.** In two drills the approval arrived after the 15 minutes (once the operator stepped away, once the laptop went to sleep). Both times the late `approve` was refused (`action is already expired`) and payments was restored by hand. That is the intended behaviour: an approval must be about the current situation. The drill now beeps, highlights the question and gives up after 14 minutes.
- **A laptop that sleeps stops watching.** During a suspend nothing runs: no scrapes, no alerts, no expiry, not even the drill's timer. On wake everything happens in the same second (an action expired, three alerts fired and cleared at once) and a stale `ServiceDown` flickered for orders. Real monitoring runs on machines that do not sleep (Phase 10).
- **Grouping still applies.** Re-running the drill while the previous incident was still open made the new fault join that incident (ADR-0006), so the drill now waits for the open incident to close first.
- **Symptoms of the caller.** `UpstreamErrors` is raised by `orders` about its upstream, so it now counts as an alert on orders too; the validator had dropped orders as an unsupported symptom.

## Web approval console (Phase 7b)

`centinela console` is a small server-rendered page: pending actions with the AI diagnosis next to them, Approve and Reject (with a required reason) buttons, open incidents and recent decisions. It calls the same `actions/approvals.py` as the CLI.

- **No Kubernetes rights at all** (no ServiceAccount token mounted) and its **own database role**, `centinela_console`: `SELECT`, `UPDATE` on only the four decision columns of `incident_actions` (`status, decided_by, decided_at, decision_note`) and `INSERT` on `incident_events`. Tests prove it cannot change what runs (`target`, `params`), create actions, delete the audit trail or touch incidents. Residual risk: it can write any value into `status`; the executor still only runs `approved` actions and re-checks the policy.
- **Login:** one operator account (`console_user`, recorded as the approver) with a 24-character password generated by Terraform (`terraform output -raw console_password`), compared in constant time; 5 failures lock the client for 15 minutes.
- **Session:** an HMAC-signed cookie (`user|expiry|signature`, key from Terraform), `HttpOnly`, `SameSite=Strict`, 8 hours; `Secure` once it is served over HTTPS (Phase 10). A forged or extended cookie fails the signature.
- **CSRF:** every form carries an HMAC token derived from the session; requests without it get 403. Together with `SameSite=Strict` another site cannot make the browser approve anything.
- **No JavaScript:** `Content-Security-Policy: default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'`, plus `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `no-store`. Every value from the database is HTML-escaped (log text reaches the page through the AI summary). No `/docs` or OpenAPI endpoint.
- **Not exposed:** a ClusterIP Service; locally `./scripts/k8s.sh console` port-forwards it to `127.0.0.1:8400`. Public exposure, behind HTTPS and with stricter limits, is Phase 10.

First approval from the browser (incident 9): the agent proposed `scale payments to 1 replica(s)`, the operator clicked Approve at 01:12:04 UTC ("Action 5 approved by fabricio"), and the executor reported `0/0 -> 1/1 ready after 9 s` at 01:12:17.

**A credential leak during testing, and what changed.** A setup command printed the console password in the terminal, and that output was pasted into a chat. A secret that has left the machine is no longer a secret, so it was rotated within minutes: `terraform apply -replace=random_password.console_login`, then a rollout restart of the console (environment variables are read at start), and a check that the Kubernetes secret matched the new Terraform value, without printing either. The tooling changed so it cannot happen again: `./scripts/k8s.sh console` now copies the password straight to the Windows clipboard and never displays it.

## Zero-downtime rollouts (incident 6)

`./scripts/k8s.sh up` restarted payments and opened incident 6 (`UpstreamErrors`, 49 s): during a rolling update the old pod keeps receiving requests for a moment after it starts shutting down, and the alert fired on **any** failed call. Two fixes: the shop containers now wait 5 s in a `preStop` hook (the endpoint is removed before the process stops), and `UpstreamErrors` needs more than 0.1 failed calls per second, with a `promtool` test showing that a rollout blip (0.04/s) no longer fires while a real outage (1.5/s) does.

## Consequences

- MTTR can now drop to "time for a human to read one line and press approve". The incident drill asks for the approval in the terminal and measures it.
- The human is still the bottleneck, on purpose. Auto-approval for low-risk actions could come later, only with evidence from Phase 9 that the agent is right often enough.
- There are now two ways to decide (CLI now, web page in 7b), one code path (`actions/approvals.py`), so both follow exactly the same rules.
- The executor's Role names its four targets explicitly. Adding a service to the shop means updating `policy.py` *and* Terraform: deliberate friction.
