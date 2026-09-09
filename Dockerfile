FROM node:22-bookworm-slim AS frontend

WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim AS application

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app
RUN groupadd --gid 10001 football \
    && useradd --uid 10001 --gid football --create-home football \
    && mkdir -p /data /app/frontend \
    && chown -R football:football /data /app

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY backend/ ./backend/
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2.1,<27" \
    && python -m pip install --no-cache-dir ".[push,ml]"
COPY --from=frontend /build/frontend/dist ./frontend/dist

USER football
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3)"]
CMD ["python", "backend/start.py"]
