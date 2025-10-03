#!/bin/bash
# Script para iniciar el entorno de desarrollo con Upstash

echo "🚀 INICIANDO ENTORNO DE DESARROLLO CON UPSTASH"
echo "============================================="

# Cargar variables de entorno desde .env
if [ -f .env ]; then
    echo "📄 Cargando variables de entorno desde .env..."
    export $(grep -v '^#' .env | xargs)
else
    echo "❌ Archivo .env no encontrado"
    exit 1
fi

# Verificar variables de entorno
echo "🔍 Verificando configuración..."
if [ -z "$REDIS_URL" ]; then
    echo "❌ REDIS_URL no está configurada"
    exit 1
fi

if [ -z "$QDRANT_URL" ]; then
    echo "❌ QDRANT_URL no está configurada"
    exit 1
fi

echo "✅ REDIS_URL: ${REDIS_URL:0:50}..."
echo "✅ QDRANT_URL: $QDRANT_URL"

# Verificar conexión a Redis Upstash
echo "📡 Verificando conexión a Redis Upstash..."
if ! poetry run python -c "
import redis
import os
try:
    r = redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)
    r.ping()
    print('✅ Conexión a Redis Upstash exitosa')
except Exception as e:
    print(f'❌ Error conectando a Redis Upstash: {e}')
    exit(1)
"; then
    echo "❌ No se pudo conectar a Redis Upstash"
    exit 1
fi

# Verificar conexión a Qdrant Upstash
echo "🔍 Verificando conexión a Qdrant Upstash..."
if ! poetry run python -c "
import os
try:
    from qdrant_client import QdrantClient
    client = QdrantClient(url=os.getenv('QDRANT_URL'))
    collections = client.get_collections()
    print('✅ Conexión a Qdrant Upstash exitosa')
except Exception as e:
    print(f'❌ Error conectando a Qdrant Upstash: {e}')
    exit(1)
"; then
    echo "❌ No se pudo conectar a Qdrant Upstash"
    exit 1
fi

# Iniciar worker de RQ con Upstash (todas las colas)
echo "👷 Iniciando worker de RQ con Upstash (todas las colas)..."
echo "   📊 Colas: ingest, doc_parse_light, doc_parse_heavy, embeddings"
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES poetry run rq worker ingest doc_parse_light doc_parse_heavy embeddings --url "$REDIS_URL" &
WORKER_PID=$!
echo "✅ Worker unificado iniciado con PID: $WORKER_PID"

# Iniciar servidor FastAPI
echo "🌐 Iniciando servidor FastAPI..."
poetry run dev &
SERVER_PID=$!
echo "✅ Servidor iniciado con PID: $SERVER_PID"

echo ""
echo "🎉 ENTORNO UPSTASH INICIADO"
echo "==========================="
echo "📡 Redis: Upstash Cloud"
echo "🔍 Qdrant: Upstash Cloud"
echo "👷 Worker RQ: PID $WORKER_PID"
echo "🌐 Servidor: http://localhost:4000"
echo "📚 Documentación: http://localhost:4000/docs"
echo ""
echo "Para detener todo:"
echo "  kill $WORKER_PID $SERVER_PID"
echo ""

# Mantener el script corriendo
wait
