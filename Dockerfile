FROM python:3.13.5-slim-bookworm

LABEL maintainer="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.version="1.2.2"
LABEL org.opencontainers.image.authors="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.source="https://github.com/0x212121/b-snap"

# Install ffmpeg + Liberation Sans font
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fontconfig \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Test ffmpeg install
RUN ffprobe -version

COPY . /app

EXPOSE 8080

# Gunicorn for multi-core performance
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8080", "--workers", "2"]
