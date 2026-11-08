# One small image for the three shop services (catalog, orders, payments).
# Build from the repository root:  docker build -f apps/shop/Dockerfile -t centinela-shop:dev .

FROM ghcr.io/astral-sh/uv:0.11 AS uv

# ---- build stage: resolve dependencies with uv -----------------------------------------
FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 1) dependencies only (cached layer: rebuilds are fast when only code changes)
COPY pyproject.toml uv.lock ./
COPY apps/shop/pyproject.toml apps/shop/pyproject.toml
RUN uv sync --frozen --no-dev --package shop --no-install-workspace

# 2) the shop code itself
COPY apps/shop/src apps/shop/src
RUN uv sync --frozen --no-dev --package shop --no-editable

# ---- runtime stage: no uv, no build tools, non-root user -------------------------------
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# Each service overrides the module in compose.yaml (shop.catalog / shop.orders / shop.payments)
CMD ["uvicorn", "shop.catalog:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
