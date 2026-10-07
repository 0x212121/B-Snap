# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.14.7-slim

FROM ${PYTHON_IMAGE} AS python-wheels
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_PREFER_BINARY=1

# Compilers and headers stay in the builder; both Python stages share the same ABI.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cargo rustc libffi-dev libjpeg62-turbo-dev libfreetype6-dev \
    libxml2-dev libxslt1-dev zlib1g-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip setuptools wheel && \
    python -m pip wheel --prefer-binary --wheel-dir=/wheels -r requirements.txt

FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --include=dev --no-audit --no-fund
COPY tailwind.config.js postcss.config.js ./
COPY assets/ ./assets/
COPY templates/ ./templates/
COPY app/ ./app/
COPY static/js/ ./static/js/
RUN mkdir -p static/css && npm run build -- --minify

FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=2.4.0
LABEL maintainer="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.version="${APP_VERSION}"
LABEL org.opencontainers.image.authors="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.source="https://github.com/0x212121/b-snap"

WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Keep native runtime libraries plus camera, media, healthcheck and mount tooling.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-liberation fontconfig curl cifs-utils nfs-common \
    libffi8 libjpeg62-turbo libfreetype6 libxml2 libxslt1.1 zlib1g libpq5 libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
# Mount wheels from the builder so they do not become a layer in the final image.
RUN --mount=type=bind,from=python-wheels,source=/wheels,target=/wheels \
    python -m pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/requirements.txt && \
    python -m pip check && rm /tmp/requirements.txt

COPY . /app
COPY --from=frontend /build/static/css/output.css /app/static/css/output.css
RUN python scripts/check_image_version.py "${APP_VERSION}" && \
    python scripts/check_runtime_imports.py && \
    DATABASE_URL=postgresql+psycopg2://image_check@127.0.0.1:1/image_check SECRET_KEY=image-build-check ENVIRONMENT=testing python -c "import app.main" && \
    python -c "import cv2, psycopg2, asyncpg; from PIL import Image; from cryptography.fernet import Fernet" && \
    ffprobe -version && \
    python -c "from pathlib import Path; paths = [Path('start.sh'), Path('gunicorn.conf.py')]; [p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n')) for p in paths]" && \
    chmod +x /app/start.sh && test -s /app/static/css/output.css

EXPOSE 8080
CMD ["/app/start.sh"]
