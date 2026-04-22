FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg gcc libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

ENV DATA_DIR=/data
EXPOSE 8000

CMD ["sh", "-c", \
     "python -c \"from app import _reset_orphaned_generating; _reset_orphaned_generating()\" \
     && gunicorn app:app \
          --bind 0.0.0.0:8000 \
          --worker-class gthread \
          --workers 2 \
          --threads 4 \
          --timeout 300 \
          --access-logfile - \
          --error-logfile -"]
