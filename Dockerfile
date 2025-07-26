# Dockerfile (en la raíz de backend_tensor)
FROM python:3.13-slim

WORKDIR /app

# 1) Copiamos sólo pyproject.toml y poetry.lock para cachear deps
COPY pyproject.toml poetry.lock /app/

# 2) Instalamos Poetry y las deps de producción (sin el paquete .)
RUN pip install poetry \
 && poetry config virtualenvs.create false \
 && poetry install --no-root --only main

# 3) Ahora sí copiamos TODO el código
COPY . /app

# 4) Exponemos el puerto de la API
EXPOSE 4000

# 5) Definimos command por defecto (la API). El worker se lanzará
#    desde docker-compose con otro command distinto.
CMD ["poetry", "run", "dev"]
