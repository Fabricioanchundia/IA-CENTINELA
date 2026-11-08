"""PostgreSQL connection pool helper (small pool: this runs on a 16 GB laptop)."""

import logging

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("shop.db")

Pool = ConnectionPool[Connection[DictRow]]


def make_pool(database_url: str, schema_sql: str) -> Pool:
    pool: Pool = ConnectionPool(
        database_url,
        connection_class=Connection[DictRow],
        kwargs={"row_factory": dict_row},
        min_size=1,
        max_size=4,
        open=True,
        timeout=5,
    )
    with pool.connection() as conn:
        conn.execute(schema_sql)
    log.info("database ready")
    return pool


def ping(pool: Pool) -> str:
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception:
        log.exception("database ping failed")
        return "fail"
