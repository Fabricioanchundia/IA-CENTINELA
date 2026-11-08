"""Steady background traffic so dashboards and alerts have something to show.

Every tick it browses the catalog and places an order. About 1 in 10 orders asks for a
product that does not exist (a normal 404, not an incident). It never stops on errors:
when a service is down it keeps knocking, exactly like real users would.

    python -m shop.loadgen     # LOADGEN_RPS=2 ORDERS_URL=... CATALOG_URL=...
"""

import logging
import os
import random
import signal
import time
from collections import Counter
from types import FrameType

import httpx

from shop.logs import configure_logging

log = logging.getLogger("shop.loadgen")

PRODUCT_IDS = (1, 2, 3, 4, 5)
MISSING_PRODUCT_ID = 999


def pick_order(rng: random.Random) -> dict[str, int]:
    product = MISSING_PRODUCT_ID if rng.random() < 0.1 else rng.choice(PRODUCT_IDS)
    return {"product_id": product, "quantity": rng.randint(1, 3)}


def run(
    orders_url: str,
    catalog_url: str,
    rps: float,
    max_ticks: int | None = None,
    rng: random.Random | None = None,
) -> Counter[str]:
    """Send traffic until stopped (or `max_ticks`, used by tests). Returns status counts."""
    rng = rng or random.Random()  # noqa: S311 - not used for security
    stats: Counter[str] = Counter()
    stop = {"now": False}

    def handle_stop(signum: int, frame: FrameType | None) -> None:
        stop["now"] = True

    signal.signal(signal.SIGTERM, handle_stop)
    interval = 1.0 / max(rps, 0.1)
    ticks = 0
    with httpx.Client(timeout=3.0) as http:
        while not stop["now"] and (max_ticks is None or ticks < max_ticks):
            started = time.monotonic()
            for method, url, body in (
                ("GET", f"{catalog_url}/products", None),
                ("POST", f"{orders_url}/orders", pick_order(rng)),
            ):
                try:
                    resp = http.request(method, url, json=body)
                    stats[str(resp.status_code)] += 1
                except httpx.HTTPError as exc:
                    stats[type(exc).__name__] += 1
            ticks += 1
            if ticks % 100 == 0:
                log.info("loadgen progress", extra={"fields": {"ticks": ticks, **stats}})
            if max_ticks is None:
                time.sleep(max(0.0, interval - (time.monotonic() - started)))
    return stats


def main() -> None:
    configure_logging("loadgen")
    rps = float(os.environ.get("LOADGEN_RPS", "2"))
    orders_url = os.environ.get("ORDERS_URL", "http://localhost:8002")
    catalog_url = os.environ.get("CATALOG_URL", "http://localhost:8001")
    log.info("loadgen started", extra={"fields": {"rps": rps}})
    run(orders_url, catalog_url, rps)
    log.info("loadgen stopped")


if __name__ == "__main__":
    main()
