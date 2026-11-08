# ADR-0002: Resource budget for a 16 GB laptop

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Development machine: Intel i7-14650HX, **16 GB RAM**, RTX 5060 Laptop GPU (**8 GB VRAM**), Windows 11 + WSL2. Windows and everyday apps already use ~6–10 GB. The full target stack (microservices, PostgreSQL, Redis, Prometheus, Grafana, Loki, Kubernetes, agent, LLM) could easily exceed the remaining memory and freeze the laptop.

## Decision

1. **LLM on the GPU, outside WSL.** Ollama runs natively on Windows; models (`qwen2.5:7b`, ~5 GB) live in VRAM. WSL reaches it at `localhost:11434` thanks to `networkingMode=mirrored`.
2. **Hard cap on WSL2** via `.wslconfig`: `memory=6GB` now, raised to `8GB` only when Kubernetes (k3d) arrives in Phase 4. `swap=4GB` as a safety net.
3. **Memory limits on every container** (`mem_limit` in Compose, `resources.limits` in Kubernetes). A chaos "memory leak" can never take more than its limit.
4. **Turn on only what each phase needs** with Docker Compose profiles; the Compose version is switched off once the same app runs in k3d.
5. **Lightweight choices:** k3d with a single server node, Prometheus with short retention, Loki in single-binary mode.
6. **Measure, don't guess:** `free -h`, `docker stats`, `htop` and Task Manager (`VmmemWSL`) before and after each phase.

## Consequences

- Estimated peak inside WSL ≈ 5.5–6 GB (Phase 9), within the cap.
- If something does not fit: first lighter versions, then a free cloud tier, then show that part in the video. Buying RAM is the last resort.
- The constraint itself is a talking point: capacity planning and resource limits are core SRE skills.
