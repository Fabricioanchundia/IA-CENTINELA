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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
            loki_url=os.environ.get("LOKI_URL", "http://localhost:3100"),
            database_url=os.environ.get("DATABASE_URL", ""),
            reader_password=os.environ.get("INCIDENTS_DB_READER_PASSWORD") or None,
            poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "15")),
            metrics_port=int(os.environ.get("METRICS_PORT", "9100")),
        )
