"""Settings for the agent side of Centinela, read from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    prometheus_url: str
    loki_url: str
    database_url: str  # empty: libpq PG* variables
    reader_password: str | None
    poll_interval_s: float
    metrics_port: int
    ollama_url: str
    ollama_model: str
    ollama_num_ctx: int
    ollama_think: bool
    diagnose_delay_s: float
    runbooks_dir: str
    shop_namespace: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
            loki_url=os.environ.get("LOKI_URL", "http://localhost:3100"),
            database_url=os.environ.get("DATABASE_URL", ""),
            reader_password=os.environ.get("INCIDENTS_DB_READER_PASSWORD") or None,
            poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "15")),
            metrics_port=int(os.environ.get("METRICS_PORT", "9100")),
            ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
            ollama_num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
            ollama_think=os.environ.get("OLLAMA_THINK", "false").lower() in {"1", "true", "yes"},
            diagnose_delay_s=float(os.environ.get("DIAGNOSE_DELAY_S", "60")),
            runbooks_dir=os.environ.get("RUNBOOKS_DIR", "docs/runbooks"),
            shop_namespace=os.environ.get("SHOP_NAMESPACE", "shop"),
        )
