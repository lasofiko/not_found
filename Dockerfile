# syntax=docker/dockerfile:1
FROM node:24.14.0-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/ ./backend/
COPY ml/ ./ml/
COPY pipeline/ ./pipeline/
# Метрики жюри и итоговых моделей сайта (docs/final): по ним сайт показывает качество месячных моделей.
COPY docs/jury/ ./docs/jury/
COPY docs/final/ ./docs/final/
# Keep build inputs: the backend verifies dist against their SHA-256.
COPY frontend/ ./frontend/
COPY --from=frontend-build /app/frontend/dist/ ./frontend/dist/
RUN mkdir -p /app/models && chown 10001:10001 /app/models
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=5s --timeout=4s --start-period=10s --retries=6 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:8000' + os.environ.get('BB_API_PREFIX', '/api/v1') + '/health', timeout=3).close()"]
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
