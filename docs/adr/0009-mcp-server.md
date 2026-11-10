# ADR-0009: MCP server: other AI assistants can read and propose, never act (Phase 7c)

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Engineers increasingly work through AI assistants (Claude Desktop, IDE copilots). The Model Context Protocol (MCP) is the standard way to give those assistants tools. Exposing Centinela over MCP lets an engineer ask "what is broken and why?" from their assistant and get Centinela's incidents, diagnoses and audit trail. But an MCP client is another language model we do not control, possibly fed with untrusted text, so it must not get more power than the agent itself.

## Decision

- **Four tools** on the official Python SDK (`mcp` 2.x, `MCPServer`, stdio transport):
  - `list_incidents`, `get_incident` (alerts, latest AI diagnosis, actions), `list_actions`, all marked `readOnlyHint`;
  - `propose_action(incident_id, kind, target, reason)`, which creates a **pending** action like the agent's.
  - **No approve or execute tool, on purpose.** A human approves in the console or the CLI; the executor runs it (ADR-0008).
- **Same policy as everything else:** `propose_action` calls `actions/policy.py` (allowlist, never PostgreSQL), refuses resolved or unknown incidents, requires a 10–500 character reason, and is subject to "one open action per incident". Proposals are recorded as `proposed_by = mcp:<client>`; names starting with `mcp` can never approve.
- **Its own database role, `centinela_mcp`:** `SELECT`, plus `INSERT` on exactly the proposal columns of `incident_actions`. `status` is not among them, so every row it creates starts as `pending`: even with stolen credentials it cannot insert an already-approved action, update or delete one (tested against PostgreSQL).
- **Runs inside the cluster, reached through `kubectl exec`.** The `centinela-mcp` pod idles (`sleep infinity`) with the code and the `mcp-credentials` secret; each client session runs `kubectl exec -i deploy/centinela-mcp -c mcp -- centinela mcp`. No Service, no open port, no Kubernetes token in the pod: only someone who can already `exec` in the cluster can start a session. `./scripts/k8s.sh mcp` prints the client configuration; `scripts/mcp-check.py` is a smoke test that connects the same way.
- **Untrusted text is labelled.** Fields that carry text produced by the shop (alert summaries, the AI summary that may quote logs) are named `*_untrusted`, every response carries a note that they are data, not instructions, and so do the server instructions. Unexpected errors are reported to the client without stack traces.

## Consequences

- A second AI can collaborate with Centinela with exactly the agent's power level: read and propose. The security boundary is the same three-identity design, enforced by database grants and Kubernetes RBAC, not by trusting the client.
- A malicious or confused MCP client can, at worst, create one pending proposal per open incident inside the allowlist, which expires in 15 minutes unless a human approves it.
- `kubectl exec` access is the authentication. That is fine for a local cluster operated by its owner; a remote MCP endpoint (streamable HTTP with OAuth) is out of scope until Phase 10.
- Phase 8 will attack both the agent and the MCP path with prompt injection hidden in logs.
