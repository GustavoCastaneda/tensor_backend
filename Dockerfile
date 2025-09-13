# backend_tensor/Dockerfile - Optimizado para ECS Fargate
FROM python:3.13-slim

# -------- SO deps + Tesseract (OCR) ----------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential gcc libpq-dev \
      tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# -------- Usuario no-root para seguridad ----------
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

# -------- Workspace ---------------------------------------------------
WORKDIR /app

# 1) Copiamos manifests para cachear deps
COPY --chown=app:app pyproject.toml poetry.lock /app/

# 2) Poetry + deps del proyecto (sin venv)
RUN pip install --no-cache-dir --user poetry && \
    ~/.local/bin/poetry config virtualenvs.create false && \
    ~/.local/bin/poetry install --no-root --only main

# 2.1) Docling + fallbacks (pypdf, python-docx)
RUN pip install --no-cache-dir --user docling pypdf python-docx

# 3) Código
COPY --chown=app:app . /app

# 4) Puerto expuesto (FastAPI en 4000)
EXPOSE 4000

# 5) Comando por defecto (API).
#    Los servicios `worker` y `worker_docs` en docker-compose lo sobreescriben.
CMD ["~/.local/bin/poetry", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4000", "--workers", "1"]
