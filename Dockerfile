FROM python:3.13.6-slim

LABEL maintainer="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.version="1.15.0"
LABEL org.opencontainers.image.authors="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.source="https://github.com/0x212121/b-snap"

# Install ffmpeg + Liberation Sans font + curl for healthcheck + dos2unix for line endings
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fontconfig \
        curl \
        dos2unix \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install reportlab

# Test ffmpeg install
RUN ffprobe -version

COPY . /app

# Convert Windows line endings to Unix (CRLF to LF) and ensure executable permissions
RUN dos2unix /app/start.sh && \
    chmod +x /app/start.sh && \
    dos2unix /app/gunicorn.conf.py 2>/dev/null || true

EXPOSE 8080
`
CMD ["/app/start.sh"]