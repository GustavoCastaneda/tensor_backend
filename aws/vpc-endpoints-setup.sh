#!/bin/bash

# Script para crear VPC Endpoints necesarios para Tensor Docling Worker
# Esto elimina la necesidad de NAT Gateway para servicios AWS

set -e

# Configuración - REEMPLAZA con tus valores
VPC_ID="vpc-REPLACE_WITH_YOUR_VPC_ID"
SUBNET_IDS="subnet-REPLACE_WITH_YOUR_SUBNET_1,subnet-REPLACE_WITH_YOUR_SUBNET_2"
SECURITY_GROUP_ID="sg-REPLACE_WITH_YOUR_SECURITY_GROUP"
REGION="us-east-1"

echo "🚀 Creando VPC Endpoints para Tensor Docling Worker"
echo "VPC: $VPC_ID"
echo "Región: $REGION"

# Función para crear VPC Endpoint
create_vpc_endpoint() {
    local service_name=$1
    local vpc_endpoint_type=$2
    local service_type=$3

    echo "📍 Creando VPC Endpoint para $service_name..."

    if [ "$vpc_endpoint_type" = "Gateway" ]; then
        # Gateway Endpoint (para S3)
        aws ec2 create-vpc-endpoint \
            --vpc-id $VPC_ID \
            --service-name $service_name \
            --route-table-ids $(aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$VPC_ID" --query 'RouteTables[0].RouteTableId' --output text) \
            --region $REGION \
            --no-cli-pager
    else
        # Interface Endpoint
        aws ec2 create-vpc-endpoint \
            --vpc-id $VPC_ID \
            --vpc-endpoint-type Interface \
            --service-name $service_name \
            --subnet-ids $SUBNET_IDS \
            --security-group-ids $SECURITY_GROUP_ID \
            --region $REGION \
            --no-cli-pager
    fi

    echo "✅ VPC Endpoint creado: $service_name"
}

# VPC Endpoints recomendados para Tensor Docling Worker
echo "🔧 Creando VPC Endpoints..."

# ECR (Interface) - Para descargar imágenes Docker
create_vpc_endpoint "com.amazonaws.$REGION.ecr.api" "Interface" "ECR"
create_vpc_endpoint "com.amazonaws.$REGION.ecr.dkr" "Interface" "ECR"

# S3 (Gateway) - Para almacenamiento (si necesitas)
create_vpc_endpoint "com.amazonaws.$REGION.s3" "Gateway" "S3"

# CloudWatch Logs (Interface) - Para logs
create_vpc_endpoint "com.amazonaws.$REGION.logs" "Interface" "CloudWatch"

# SQS (Interface) - Si usas colas (opcional)
# create_vpc_endpoint "com.amazonaws.$REGION.sqs" "Interface" "SQS"

echo ""
echo "🎉 ¡VPC Endpoints creados exitosamente!"
echo ""
echo "📋 Resumen:"
echo "✅ ECR API + ECR DKR - Para descargar imágenes"
echo "✅ S3 - Para almacenamiento (si necesitas)"
echo "✅ CloudWatch Logs - Para monitoreo"
echo ""
echo "💡 Ahora puedes:"
echo "   - Desactivar NAT Gateway para ahorrar costos"
echo "   - Mantener las tareas completamente privadas"
echo "   - Mejorar la seguridad de tu infraestructura"
echo ""
echo "⚠️  Nota: Si tu aplicación necesita pip install o acceder"
echo "         a servicios externos no-AWS, necesitarás:"
echo "         - NAT Gateway, O"
echo "         - Configurar un proxy/VPN, O"
echo "         - Usar imágenes Docker preconstruidas"
