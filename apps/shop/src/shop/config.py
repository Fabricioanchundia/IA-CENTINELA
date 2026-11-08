"""Settings read from environment variables (12-factor style)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    service: str
    database_url: str
    redis_url: str
    catalog_url: str
    payments_url: str
    http_timeout_s: float

    @classmethod
    def from_env(cls, service: str) -> "Settings":
        return cls(
            service=service,
            # Empty = libpq reads PGHOST, PGUSER, PGPASSWORD, PGDATABASE from the environment,
            # so no password ever lives inside a connection string or in the code.
            database_url=os.environ.get("DATABASE_URL", ""),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            catalog_url=os.environ.get("CATALOG_URL", "http://localhost:8001"),
            payments_url=os.environ.get("PAYMENTS_URL", "http://localhost:8003"),
            http_timeout_s=float(os.environ.get("HTTP_TIMEOUT_S", "2.0")),
        )
