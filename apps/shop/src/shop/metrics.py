"""Prometheus metrics for the shop (RED method: Rate, Errors, Duration + a few business ones).

Labels are kept low-cardinality on purpose: the route *template* (`/orders/{order_id}`),
never the raw path, and no user or order IDs. The `service` label is added by Prometheus
from the scrape target, so it is not repeated here.
"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by route and status code.",
    ["method", "route", "status"],
)

HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

ORDERS = Counter(
    "shop_orders_total",
    "Orders by final status (paid, declined, failed).",
    ["status"],
)

UPSTREAM_ERRORS = Counter(
    "shop_upstream_errors_total",
    "Failed calls from orders to a dependency.",
    ["upstream"],
)

CACHE_REQUESTS = Counter(
    "shop_cache_requests_total",
    "Catalog cache lookups by result (hit, miss, error).",
    ["result"],
)
