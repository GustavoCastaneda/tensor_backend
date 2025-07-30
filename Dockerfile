# backend_tensor/Dockerfile
FROM python:3.13-slim

# -------- SO deps mínimos (compiladores + libpq para psycopg) ----------
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# -------- Workspace ---------------------------------------------------
WORKDIR /app

# 1) Copiamos primero los manifests para cachear dependencias
COPY pyproject.toml poetry.lock /app/

# 2) Instalamos Poetry y deps de producción
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --only main

# 3) Copiamos el resto del código
COPY . /app

# 4) Puerto expuesto (FastAPI en 4000)
EXPOSE 4000

# 5) Comando por defecto (API).  
#    El servicio `worker` del docker-compose lo sobreescribe.
CMD ["poetry", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4000", "--reload"]
