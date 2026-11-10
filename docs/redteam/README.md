# Red team results

Output of `uv run centinela redteam` (Phase 8, [ADR-0010](../adr/0010-prompt-injection-red-team.md)): prompt-injection attacks hidden in logs, Kubernetes events and postmortems, run against three versions of the agent on a local model.

| File | What |
|---|---|
| `results-<model>-<incident>.md` | summary and one row per attack, per defence level |
| `results-<model>-<incident>.json` | every answer: root cause, recommended action, what would reach the human, quarantined text, seconds |

Re-run after any change to the prompt, the model or the guards (needs only Ollama, about 10 minutes):

```bash
uv run centinela redteam                          # both incidents, all levels
uv run centinela redteam --scenario slow-orders   # one incident
OLLAMA_MODEL=qwen2.5:7b uv run centinela redteam  # another model
uv run centinela redteam --list                   # the attacks
```
