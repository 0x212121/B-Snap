FROM python:3.13.5-slim-bookworm

LABEL maintainer="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.version="1.0.0-multi"
LABEL org.opencontainers.image.authors="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.source="https://github.com/0x212121/b-snap"

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN ffprobe -version

COPY . /app

EXPOSE 8080

# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]

# Multi core CPU
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8080", "--workers", "2"]

