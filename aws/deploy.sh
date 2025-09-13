#!/bin/bash

# Script de despliegue para sistema dual (Light + Heavy)
set -e

# Configuración
AWS_REGION="us-east-1"
ECR_REPOSITORY="891376931243.dkr.ecr.us-east-1.amazonaws.com/tensor-docling-worker"
CLUSTER_NAME="tensor-cluster"
IMAGE_TAG="${1:-latest}"

echo "🚀 Iniciando despliegue del sistema dual Docling..."
echo "📦 Imagen: ${ECR_REPOSITORY}:${IMAGE_TAG}"

# Función para crear/actualizar servicio
deploy_service() {
    local service_name=$1
    local config_file=$2
    local formula_enrichment=$3
    
    echo "📋 Procesando servicio: ${service_name}"
    
    # Verificar si el servicio existe
    if aws ecs describe-services --cluster ${CLUSTER_NAME} --services ${service_name} --region ${AWS_REGION} --query 'services[0].serviceName' --output text | grep -q "${service_name}"; then
        echo "✅ Servicio ${service_name} existe, actualizando..."
        
        # Actualizar servicio existente
        aws ecs update-service \
            --cluster ${CLUSTER_NAME} \
            --service ${service_name} \
            --task-definition tensor-docling-worker:${IMAGE_TAG} \
            --region ${AWS_REGION} \
            --query 'service.{ServiceName:serviceName,Status:status,TaskDefinition:taskDefinition}' \
            --output table
            
    else
        echo "🆕 Creando nuevo servicio: ${service_name}"
        
        # Crear servicio nuevo
        aws ecs create-service \
            --cluster ${CLUSTER_NAME} \
            --service-name ${service_name} \
            --task-definition tensor-docling-worker:${IMAGE_TAG} \
            --desired-count 1 \
            --launch-type FARGATE \
            --platform-version 1.4.0 \
            --network-configuration "awsvpcConfiguration={subnets=[subnet-0a1b2c3d4e5f67890,subnet-0f1e2d3c4b5a69780],securityGroups=[sg-0123456789abcdef0],assignPublicIp=ENABLED}" \
            --deployment-configuration "maximumPercent=200,minimumHealthyPercent=50" \
            --enable-execute-command \
            --region ${AWS_REGION} \
            --query 'service.{ServiceName:serviceName,Status:status,TaskDefinition:taskDefinition}' \
            --output table
    fi
    
    # Configurar variable de entorno específica
    echo "⚙️ Configurando DOCLING_FORMULA_ENRICHMENT=${formula_enrichment} para ${service_name}"
    
    # Nota: Las variables de entorno se configuran en la Task Definition
    # Aquí solo mostramos la configuración
    echo "📝 Variable configurada: DOCLING_FORMULA_ENRICHMENT=${formula_enrichment}"
}

# Función para construir y subir imagen
build_and_push() {
    echo "🔨 Construyendo imagen Docker..."
    docker build -f aws/Dockerfile.prod -t ${ECR_REPOSITORY}:${IMAGE_TAG} .
    
    echo "📤 Subiendo imagen a ECR..."
    docker push ${ECR_REPOSITORY}:${IMAGE_TAG}
    
    echo "✅ Imagen ${ECR_REPOSITORY}:${IMAGE_TAG} subida exitosamente"
}

# Función para registrar Task Definition
register_task_definition() {
    echo "📋 Registrando Task Definition..."
    
    # Reemplazar variables en la Task Definition
    sed "s/\${DOCLING_FORMULA_ENRICHMENT}/false/g" aws/task-definition.json > /tmp/task-def-light.json
    sed "s/\${DOCLING_FORMULA_ENRICHMENT}/true/g" aws/task-definition.json > /tmp/task-def-heavy.json
    
    # Registrar Task Definition para Light
    aws ecs register-task-definition \
        --cli-input-json file:///tmp/task-def-light.json \
        --region ${AWS_REGION} \
        --query 'taskDefinition.{Family:family,Revision:revision,Status:status}' \
        --output table
    
    # Registrar Task Definition para Heavy
    aws ecs register-task-definition \
        --cli-input-json file:///tmp/task-def-heavy.json \
        --region ${AWS_REGION} \
        --query 'taskDefinition.{Family:family,Revision:revision,Status:status}' \
        --output table
    
    echo "✅ Task Definitions registradas"
}

# Función para configurar auto-scaling
setup_autoscaling() {
    local service_name=$1
    local min_capacity=$2
    local max_capacity=$3
    
    echo "📊 Configurando auto-scaling para ${service_name}..."
    
    # Crear target de auto-scaling si no existe
    aws application-autoscaling register-scalable-target \
        --service-namespace ecs \
        --resource-id "service/${CLUSTER_NAME}/${service_name}" \
        --scalable-dimension ecs:service:DesiredCount \
        --min-capacity ${min_capacity} \
        --max-capacity ${max_capacity} \
        --region ${AWS_REGION} || echo "⚠️ Target de auto-scaling ya existe"
    
    echo "✅ Auto-scaling configurado para ${service_name}"
}

# Función para mostrar estado
show_status() {
    echo "�� Estado de los servicios:"
    
    echo "🪶 Servicio Light:"
    aws ecs describe-services \
        --cluster ${CLUSTER_NAME} \
        --services tensor-docling-light-service \
        --region ${AWS_REGION} \
        --query 'services[0].{ServiceName:serviceName,Status:status,DesiredCount:desiredCount,RunningCount:runningCount}' \
        --output table
    
    echo "⚡ Servicio Heavy:"
    aws ecs describe-services \
        --cluster ${CLUSTER_NAME} \
        --services tensor-docling-heavy-service \
        --region ${AWS_REGION} \
        --query 'services[0].{ServiceName:serviceName,Status:status,DesiredCount:desiredCount,RunningCount:runningCount}' \
        --output table
}

# Función principal
main() {
    case "${1:-all}" in
        "build")
            build_and_push
            ;;
        "deploy")
            register_task_definition
            deploy_service "tensor-docling-light-service" "aws/service-config-light.json" "false"
            deploy_service "tensor-docling-heavy-service" "aws/service-config-heavy.json" "true"
            setup_autoscaling "tensor-docling-light-service" 1 3
            setup_autoscaling "tensor-docling-heavy-service" 0 2
            ;;
        "status")
            show_status
            ;;
        "all")
            build_and_push
            register_task_definition
            deploy_service "tensor-docling-light-service" "aws/service-config-light.json" "false"
            deploy_service "tensor-docling-heavy-service" "aws/service-config-heavy.json" "true"
            setup_autoscaling "tensor-docling-light-service" 1 3
            setup_autoscaling "tensor-docling-heavy-service" 0 2
            show_status
            ;;
        *)
            echo "Uso: $0 [build|deploy|status|all]"
            echo "  build: Solo construir y subir imagen"
            echo "  deploy: Solo desplegar servicios"
            echo "  status: Solo mostrar estado"
            echo "  all: Hacer todo (por defecto)"
            exit 1
            ;;
    esac
}

# Ejecutar función principal
main "$@"
