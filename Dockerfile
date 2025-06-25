FROM python:3.13.5-slim-bookworm

RUN apt-get update && \
    apt-get install -y ffmpeg ffprobe && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app /app
COPY ./static/snapshots /static/snapshots
COPY ./static/videos /static/videos

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
