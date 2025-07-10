FROM python:3.13.5-slim-bookworm

LABEL maintainer="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.authors="Indra W. <wijaya.indra2196@gmail.com>"
LABEL org.opencontainers.image.source="https://github.com/0x212121/b-snap"

RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
