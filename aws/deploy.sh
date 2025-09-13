#!/bin/bash

# Script de despliegue para Tensor Docling Worker en ECS Fargate
# Uso: ./deploy.sh [build|deploy|update]

set -e

# Configuración
AWS_REGION="us-east-1"
AWS_PROFILE="tensor-deployer"
export AWS_PROFILE
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO_NAME="tensor-docling-worker"
CLUSTER_NAME="tensor-cluster"
SERVICE_NAME="tensor-docling-service"

echo "🚀 Iniciando despliegue de Tensor Docling Worker"
echo "Región: $AWS_REGION"
echo "Cuenta: $ACCOUNT_ID"

case "$1" in
    "build")
        echo "📦 Construyendo imagen Docker..."

        # Login a ECR
        aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

        # Crear repositorio si no existe
        aws ecr describe-repositories --repository-names $REPO_NAME --region $AWS_REGION || \
        aws ecr create-repository --repository-name $REPO_NAME --region $AWS_REGION

        # Build y push (prefer buildx amd64 si está disponible)
        if docker buildx version >/dev/null 2>&1; then
          docker buildx build --platform linux/amd64 -f aws/Dockerfile.prod -t $REPO_NAME .
        else
          docker build -f aws/Dockerfile.prod -t $REPO_NAME .
        fi
        docker tag $REPO_NAME:latest $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME:latest
        docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME:latest

        echo "✅ Imagen subida a ECR"
        ;;

    "deploy")
        echo "🚀 Desplegando servicio ECS..."

        # Crear cluster si no existe
        aws ecs describe-clusters --clusters $CLUSTER_NAME --region $AWS_REGION | jq -e '.clusters[0]' || \
        aws ecs create-cluster --cluster-name $CLUSTER_NAME --region $AWS_REGION

        # Actualizar task definition con valores reales
        sed -i '' "s/YOUR_ACCOUNT_ID/$ACCOUNT_ID/g" aws/task-definition.json

        # Registrar task definition
        aws ecs register-task-definition --cli-input-json file://aws/task-definition.json --region $AWS_REGION

        # Crear servicio
        aws ecs create-service --cli-input-json file://aws/service-config.json --region $AWS_REGION

        echo "✅ Servicio desplegado"
        ;;

    "update")
        echo "🔄 Actualizando servicio..."

        # Actualizar task definition
        aws ecs register-task-definition --cli-input-json file://aws/task-definition.json --region $AWS_REGION

        # Actualizar servicio
        aws ecs update-service --cluster $CLUSTER_NAME --service $SERVICE_NAME --force-new-deployment --region $AWS_REGION

        echo "✅ Servicio actualizado"
        ;;

    "scale")
        echo "📊 Configurando auto scaling..."

        # Crear política de auto scaling
        aws application-autoscaling register-scalable-target \
            --service-namespace ecs \
            --scalable-dimension ecs:service:DesiredCount \
            --resource-id service/$CLUSTER_NAME/$SERVICE_NAME \
            --min-capacity 1 \
            --max-capacity 5 \
            --region $AWS_REGION

        aws application-autoscaling put-scaling-policy \
            --policy-name cpu70-target-tracking-scaling-policy \
            --service-namespace ecs \
            --resource-id service/$CLUSTER_NAME/$SERVICE_NAME \
            --scalable-dimension ecs:service:DesiredCount \
            --policy-type TargetTrackingScaling \
            --target-tracking-scaling-policy-configuration file://aws/auto-scaling.json \
            --region $AWS_REGION

        echo "✅ Auto scaling configurado"
        ;;

    *)
        echo "Uso: $0 {build|deploy|update|scale}"
        echo ""
        echo "Comandos disponibles:"
        echo "  build   - Construir y subir imagen a ECR"
        echo "  deploy  - Crear cluster y servicio ECS"
        echo "  update  - Actualizar servicio con nueva imagen"
        echo "  scale   - Configurar auto scaling"
        exit 1
        ;;
esac

echo "🎉 ¡Despliegue completado!"
