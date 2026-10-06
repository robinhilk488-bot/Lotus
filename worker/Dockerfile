FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV KASSA_DATA=/data PYTHONUNBUFFERED=1
# Один процесс: фоновая синхронизация живёт внутри него. HTTPS на самоподписанном сертификате.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8765", \
     "--certfile", "/data/certs/cert.pem", "--keyfile", "/data/certs/key.pem", "app:app"]
