# Centinela image: the incident collector and the AI agent (same code, two commands).
# Build from the repository root:  docker build -t centinela-agent:dev .

FROM ghcr.io/astral-sh/uv:0.13 AS uv

# ---- build stage ----------------------------------------------------------------------
FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 1) dependencies only (cached layer). The shop's pyproject is needed because both
#    packages share one uv workspace and lock file.
COPY pyproject.toml uv.lock README.md ./
COPY apps/shop/pyproject.toml apps/shop/pyproject.toml
RUN uv sync --frozen --no-dev --package centinela --no-install-workspace

# 2) the agent code
COPY src src
RUN uv sync --frozen --no-dev --package centinela --no-editable

# ---- runtime stage: no uv, non-root ----------------------------------------------------
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
# Runbooks: the knowledge the agent quotes when it recommends an action
COPY docs/runbooks /app/runbooks
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 9100
CMD ["centinela", "collect"]
