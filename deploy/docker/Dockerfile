# 在仓库根目录执行 docker compose build。
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.14.6-slim-bookworm

ARG UV_INDEX_URL=https://pypi.org/simple
ARG UV_EXTRA_INDEX_URL=
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=127.0.0.1,localhost
ARG GIT_REVISION=unknown

LABEL org.opencontainers.image.source="https://github.com/wess09/AzurPilot" \
      org.opencontainers.image.revision="${GIT_REVISION}"

ENV UV_INDEX_URL=${UV_INDEX_URL} \
    UV_EXTRA_INDEX_URL=${UV_EXTRA_INDEX_URL} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY} \
    NO_PROXY=${NO_PROXY} \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/AzurPilot/.venv/bin:${PATH}

WORKDIR /app/AzurPilot
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update && \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 \
    install -y --no-install-recommends git adb libgomp1 libgl1 libglib2.0-0 openssh-client && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv venv --relocatable --python /usr/local/bin/python .venv && \
    uv sync --frozen --no-dev --no-install-project && \
    cp /usr/local/bin/uv .venv/bin/uv && \
    cp /usr/bin/adb .venv/bin/adb && \
    cp /usr/bin/git .venv/bin/git && \
    rm -rf /root/.cache/uv

COPY . .
COPY --from=frontend-build /frontend/dist ./frontend/dist
# 标记镜像内产物，启动时无需 Node、npm 或联网构建。
RUN .venv/bin/python -c "from pathlib import Path; from deploy.frontend import source_fingerprint; p=Path('frontend'); (p/'dist/.source-fingerprint').write_text(source_fingerprint(p))"

CMD [".venv/bin/python", "gui.py"]
