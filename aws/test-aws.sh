#!/bin/bash

# Script de prueba para verificar configuración AWS
# Uso: ./test-aws.sh

echo "🧪 Probando configuración AWS..."

# Configuración
AWS_REGION="us-east-1"
AWS_PROFILE="tensor-deployer"
export AWS_PROFILE

echo "Perfil AWS: $AWS_PROFILE"
echo "Región: $AWS_REGION"

# Verificar identidad
echo ""
echo "🔍 Verificando identidad AWS..."
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "✅ Conexión exitosa!"
    echo "Account ID: $ACCOUNT_ID"
else
    echo "❌ Error de conexión. Verifica tus credenciales."
    echo "Ejecuta: aws configure --profile tensor-deployer"
    exit 1
fi

# Verificar ECR
echo ""
echo "🔍 Verificando acceso a ECR..."
REPO_NAME="tensor-docling-worker"
aws ecr describe-repositories --repository-names $REPO_NAME --region $AWS_REGION >/dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Repositorio ECR existe"
else
    echo "ℹ️  Repositorio ECR no existe (se creará en el primer despliegue)"
fi

# Verificar ECS
echo ""
echo "🔍 Verificando acceso a ECS..."
CLUSTER_NAME="tensor-cluster"
aws ecs describe-clusters --clusters $CLUSTER_NAME --region $AWS_REGION >/dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Cluster ECS existe"
else
    echo "ℹ️  Cluster ECS no existe (se creará en el primer despliegue)"
fi

echo ""
echo "🎉 ¡Configuración AWS verificada exitosamente!"
echo ""
echo "Ahora puedes ejecutar:"
echo "  ./aws/deploy.sh build    # Para construir y subir imagen"
echo "  ./aws/deploy.sh deploy   # Para desplegar el servicio"




