# DevFlow AI application image (API + built dashboard).
# Note: to launch sandbox containers from inside this container it needs access to a
# Docker engine; see compose.yaml and docs/SECURITY.md for the trade-off. The
# recommended local setup runs the backend natively (`make serve`).

FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DEVFLOW_DATA_DIR=/data DEVFLOW_FRONTEND_DIST=/app/frontend/dist DEVFLOW_EXAMPLES_DIR=/app/examples
RUN apt-get update && apt-get install -y --no-install-recommends git docker.io \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10002 --create-home devflow
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY backend/ backend/
COPY examples/ examples/
RUN pip install .
COPY --from=frontend /build/dist /app/frontend/dist
USER devflow
EXPOSE 8000
CMD ["devflow", "serve"]
