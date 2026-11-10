"""Settings for the agent side of Centinela, read from environment variables."""

import os
from dataclasses import dataclass


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    prometheus_url: str
    loki_url: str
    database_url: str  # empty: libpq PG* variables
    reader_password: str | None
    console_db_password: str | None
    mcp_db_password: str | None
    poll_interval_s: float
    metrics_port: int
    ollama_url: str
    ollama_model: str
    ollama_num_ctx: int
    ollama_think: bool
    diagnose_delay_s: float
    runbooks_dir: str
    shop_namespace: str
    propose_actions: bool
    executor_dry_run: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
            loki_url=os.environ.get("LOKI_URL", "http://localhost:3100"),
            database_url=os.environ.get("DATABASE_URL", ""),
            reader_password=os.environ.get("INCIDENTS_DB_READER_PASSWORD") or None,
            console_db_password=os.environ.get("CONSOLE_DB_PASSWORD") or None,
            mcp_db_password=os.environ.get("MCP_DB_PASSWORD") or None,
            poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "15")),
            metrics_port=int(os.environ.get("METRICS_PORT", "9100")),
            ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
            ollama_num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
            ollama_think=_flag("OLLAMA_THINK", "false"),
            diagnose_delay_s=float(os.environ.get("DIAGNOSE_DELAY_S", "60")),
            runbooks_dir=os.environ.get("RUNBOOKS_DIR", "docs/runbooks"),
            shop_namespace=os.environ.get("SHOP_NAMESPACE", "shop"),
            propose_actions=_flag("PROPOSE_ACTIONS", "true"),
            executor_dry_run=_flag("EXECUTOR_DRY_RUN", "false"),
        )
