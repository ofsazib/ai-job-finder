# Image: ai-job-finder embedder service
# Keeps the heavy fastembed + onnxruntime deps OFF the host Python env.
# The main ai-job-finder code calls this via HTTP; nothing on the host
# needs torch / onnxruntime / fastembed installed.

FROM python:3.11-slim

# onnxruntime pulls in some libgomp etc. via apt on slim images.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first → better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the model into the image so first request is fast.
# Comment out this line during dev to avoid slow rebuilds while iterating
# on app.py; uncomment for production.
RUN python -c "from fastembed import TextEmbedding; \
    list(TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2', cache_dir='/data/models').embed(['warmup']))"

COPY app.py .

# Cache + state volume: model weights persist across container restarts
# so we don't re-download on every `docker compose up`.
VOLUME ["/data/models"]

EXPOSE 8787

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787"]
