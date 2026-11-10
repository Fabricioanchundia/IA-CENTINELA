"""Fault injection for Phase 9 (chaos engineering). Off unless an environment variable says so.

    CHAOS_LATENCY_S=1.5      every business request waits 1.5 s more (a slow release)
    CHAOS_ERROR_RATIO=0.5    half of the business requests fail with 500 (a buggy release)
    CHAOS_LEAK_KB=64         every business request keeps 64 KiB forever (a memory leak)

They are read once at start-up, so turning one on or off is a normal rollout
(`kubectl set env deploy/payments CHAOS_ERROR_RATIO=0.5`), exactly like a bad release and its
fix. Nothing can switch them over HTTP. Health, readiness and metrics endpoints are never
affected, so Kubernetes still thinks the pod is fine: the failure has to be found by
monitoring, as in real life. Log messages look like real failures and never say "chaos",
so the agent cannot read the answer in the logs.
"""

import os
import random
from dataclasses import dataclass, field


@dataclass
class Chaos:
    latency_s: float = 0.0
    error_ratio: float = 0.0
    leak_kb: int = 0
    leaked: list[bytes] = field(default_factory=list, repr=False)

    @classmethod
    def from_env(cls) -> "Chaos":
        return cls(
            latency_s=max(0.0, min(float(os.environ.get("CHAOS_LATENCY_S", "0")), 10.0)),
            error_ratio=max(0.0, min(float(os.environ.get("CHAOS_ERROR_RATIO", "0")), 1.0)),
            leak_kb=max(0, min(int(os.environ.get("CHAOS_LEAK_KB", "0")), 1024)),
        )

    @property
    def active(self) -> bool:
        return bool(self.latency_s or self.error_ratio or self.leak_kb)

    def leak(self) -> None:
        if self.leak_kb:
            # bytes filled with a non-zero value: the pages are really touched, so RSS grows
            self.leaked.append(b"\x01" * (self.leak_kb * 1024))

    def should_fail(self) -> bool:
        return self.error_ratio > 0 and random.random() < self.error_ratio  # noqa: S311 - not crypto
