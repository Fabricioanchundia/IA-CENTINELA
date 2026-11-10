# Evaluation results

Output of `uv run centinela eval run` (Phase 9, [ADR-0011](../adr/0011-chaos-and-evaluation.md)): every case in [`eval/cases/`](../../eval/cases/) replayed through the production prompt, validation and proposal guards, on local models.

| File | What |
|---|---|
| `results.md` | summary per model configuration, one row per case, and the live timings from the chaos run |
| `results.json` | every answer: root cause, category, what would reach the human, seconds, tokens |

```bash
uv run centinela eval run                                       # the default model
uv run centinela eval run --models qwen3:8b,qwen2.5:7b --think  # the comparison in ADR-0011
uv run centinela eval run --only chaos-postgres-down            # one case
uv run centinela eval synthesize                                # rewrite the synthetic cases
./scripts/chaos.sh                                              # record new cases in the cluster
```
