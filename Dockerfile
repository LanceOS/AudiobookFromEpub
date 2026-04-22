FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/app/.app_data \
    XDG_CACHE_HOME=/app/.app_data/.cache \
    HF_HOME=/app/.app_data/.cache/huggingface \
    HF_HUB_CACHE=/app/.app_data/.cache/huggingface/hub \
    HUGGINGFACE_HUB_CACHE=/app/.app_data/.cache/huggingface/hub \
    HF_TOKEN_PATH=/app/.app_data/.cache/huggingface/token \
    TRANSFORMERS_CACHE=/app/.app_data/.cache/transformers \
    TORCH_HOME=/app/.app_data/.cache/torch

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt /app/requirements-docker.txt

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchaudio \
    && pip install --no-cache-dir -r /app/requirements-docker.txt

# Kokoro/Misaki needs a small English spaCy model for phoneme/token handling.
RUN python -m spacy download en_core_web_sm

COPY . /app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.app_data /app/generated_audio \
    && chown -R appuser:appuser /app

EXPOSE 5000

ENV AUDIOBOOK_ALLOWED_OUTPUT_ROOT=/app/generated_audio \
    AUDIOBOOK_MAX_UPLOAD_MB=50 \
    AUDIOBOOK_ENABLE_CLEANUP=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3).read()"

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "5000"]
