FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV KASSA_LICENSE_DATA=/data PYTHONUNBUFFERED=1
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8900", "server:app"]
