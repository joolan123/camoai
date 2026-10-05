FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

ENV PORT=8080 \
    WS_PATH=/api/v2/stream \
    VLESS_UUID=13cac18e-685e-4cc9-ae6b-e9bbfd897c49

EXPOSE 8080

CMD ["python", "-u", "server.py"]
