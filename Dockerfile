FROM python:3.14.7-slim

LABEL maintainer="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.version="1.15.0"
LABEL org.opencontainers.image.authors="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.source="https://github.com/0x212121/b-snap"

# Install runtime packages and temporary native build dependencies for Python wheels
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fontconfig \
        curl \
        cifs-utils \
        nfs-common \
        dos2unix \
        build-essential \
        cargo \
        rustc \
        libffi-dev \
        libjpeg62-turbo-dev \
        libfreetype6-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libpq-dev \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /

COPY requirements.txt .
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_PREFER_BINARY=1
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir --prefer-binary -r requirements.txt

# Native build packages are needed only while compiling source distributions
RUN apt-get purge -y --auto-remove \
        build-essential \
        cargo \
        rustc \
        libffi-dev \
        libjpeg62-turbo-dev \
        libfreetype6-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Test ffmpeg install
RUN ffprobe -version

COPY . /app

# Convert Windows line endings to Unix (CRLF to LF) and ensure executable permissions
RUN dos2unix /app/start.sh && \
    chmod +x /app/start.sh && \
    dos2unix /app/gunicorn.conf.py 2>/dev/null || true

EXPOSE 8080

CMD ["/app/start.sh"]
