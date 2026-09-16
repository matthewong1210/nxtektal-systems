FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.13.14-slim-bookworm

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

COPY . .

RUN uv sync --locked --no-dev --extra edge-gateway --extra range-ops

RUN useradd --create-home --uid 10001 edgegateway \
    && chown -R edgegateway:edgegateway /app

USER edgegateway

CMD ["python", "-B", "scripts/edge_gateway_live_input_v0.py", "--config", "configs/edge_gateway/pilot-course-a.example.yaml"]
