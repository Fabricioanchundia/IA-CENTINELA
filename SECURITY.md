# Security Policy

Centinela is a portfolio project that runs locally and in a sandboxed demo. It is not intended for production use.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** in this repository.

I aim to answer within 7 days.

## Security principles of this project

- The agent can only run **allow-listed actions**; anything risky requires **human approval**.
- Least privilege: the agent's Kubernetes ServiceAccount has a narrow RBAC role.
- Logs and tool outputs are treated as **untrusted data**, never as instructions (prompt-injection defense).
- No secrets in the repository: `.env` is git-ignored, push protection and `gitleaks` block leaks.
- Every agent decision and action is written to an audit log.
