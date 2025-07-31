FROM python:3.13.5-slim-bookworm

LABEL maintainer="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.version="1.6.0"
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

# After COPY . /app
# COPY start.sh /app/start.sh
RUN chmod +x /app/app/start.sh

EXPOSE 8080

# Gunicorn for multi-core performance
CMD ["/app/app/start.sh"]