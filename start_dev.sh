#!/bin/bash
# Script para iniciar el entorno de desarrollo completo

echo "🚀 INICIANDO ENTORNO DE DESARROLLO COMPLETO"
echo "=========================================="

# Verificar que Redis esté funcionando
echo "📡 Verificando Redis..."
if ! redis-cli ping > /dev/null 2>&1; then
    echo "❌ Redis no está funcionando. Iniciando Redis..."
    redis-server --daemonize yes
    sleep 2
    if redis-cli ping > /dev/null 2>&1; then
        echo "✅ Redis iniciado correctamente"
    else
        echo "❌ No se pudo iniciar Redis. Instálalo con: brew install redis"
        exit 1
    fi
else
    echo "✅ Redis está funcionando"
fi

# Iniciar worker de RQ en background
echo "👷 Iniciando worker de RQ..."
poetry run rq worker ingest --url redis://localhost:6379 &
WORKER_PID=$!
echo "✅ Worker iniciado con PID: $WORKER_PID"

# Iniciar servidor FastAPI
echo "🌐 Iniciando servidor FastAPI..."
poetry run dev &
SERVER_PID=$!
echo "✅ Servidor iniciado con PID: $SERVER_PID"

echo ""
echo "🎉 ENTORNO COMPLETO INICIADO"
echo "============================"
echo "📡 Redis: localhost:6379"
echo "👷 Worker RQ: PID $WORKER_PID"
echo "🌐 Servidor: http://localhost:4000"
echo "📚 Documentación: http://localhost:4000/docs"
echo ""
echo "Para detener todo:"
echo "  kill $WORKER_PID $SERVER_PID"
echo ""

# Mantener el script corriendo
wait
