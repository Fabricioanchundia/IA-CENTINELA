"""Minimal Ollama client: one chat call that must answer with JSON matching a schema.

Ollama's structured outputs (`format` = JSON schema) constrain generation to the schema, so
the reply always parses. Whether the *content* is right is checked afterwards (diagnosis.py).
Temperature 0 and a fixed seed make runs repeatable, which the evaluation in Phase 9 needs.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class LlmError(RuntimeError):
    """The model could not be reached or did not return JSON."""


@dataclass(frozen=True)
class LlmReply:
    content: dict[str, Any]
    model: str
    prompt_tokens: int
    output_tokens: int
    seconds: float
    thinking: str | None = None


class JsonChat(Protocol):
    model: str

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LlmReply: ...


class OllamaChat:
    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int = 4096,
        think: bool = False,
        timeout_s: float = 300.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.num_ctx = num_ctx
        self.think = think
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LlmReply:
        started = time.monotonic()
        try:
            resp = self.http.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "format": schema,
                    "stream": False,
                    "think": self.think,
                    "keep_alive": "10m",
                    "options": {"temperature": 0, "seed": 42, "num_ctx": self.num_ctx},
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmError(f"ollama unreachable or failed: {exc!r}") from exc
        body = resp.json()
        message = body.get("message", {})
        raw = str(message.get("content", ""))
        try:
            content = json.loads(raw)
        except ValueError as exc:
            raise LlmError(f"model did not return JSON: {raw[:200]!r}") from exc
        if not isinstance(content, dict):
            raise LlmError("model returned JSON that is not an object")
        return LlmReply(
            content=content,
            model=str(body.get("model", self.model)),
            prompt_tokens=int(body.get("prompt_eval_count", 0)),
            output_tokens=int(body.get("eval_count", 0)),
            seconds=round(time.monotonic() - started, 2),
            thinking=message.get("thinking") or None,
        )
