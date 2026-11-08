# ADR-0001: Tooling and workflow

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Centinela combines SRE, data, AI and security. It needs a fast, reproducible Python setup and a workflow that looks like a real engineering team's, while running on a 16 GB laptop with a $0 budget.

## Decision

- **Python 3.12 + uv** for dependencies and virtual environments (one lock file, very fast installs, reproducible in CI with `uv sync --locked`).
- **src layout** (`src/centinela`) so tests run against the installed package, not loose files.
- **Ruff** for linting and formatting, including the `S` (bandit) security rules.
- **Mypy strict** to catch type errors before runtime.
- **Pytest + coverage** for tests.
- **pre-commit** with `gitleaks` so secrets are blocked before they ever reach a commit.
- **GitHub Actions** CI on every PR; heavy work runs there instead of on the laptop.
- **GitHub flow:** feature branch → Conventional Commit → PR → merge only with green CI.

## Consequences

- Same toolchain as my previous projects (Ninca, Lupa Pública, Halcón, Panal), so less setup time.
- Strict typing slows the first lines of code slightly but prevents a class of bugs in the agent's tool layer, where mistakes are costly.
