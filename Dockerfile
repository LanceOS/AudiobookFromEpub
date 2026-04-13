FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.app_data /app/output /app/generated_audio \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

ENV AUDIOBOOK_ALLOWED_OUTPUT_ROOT=/app/output \
    AUDIOBOOK_MAX_UPLOAD_MB=50 \
    AUDIOBOOK_ENABLE_CLEANUP=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3).read()"

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "5000"]
