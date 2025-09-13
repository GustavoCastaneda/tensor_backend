# 🚀 Migración de Docling a ECS Fargate

Esta guía te ayudará a migrar el procesamiento de documentos con Docling a AWS ECS Fargate.

## 📋 Prerrequisitos

- Cuenta de AWS con permisos para ECS, ECR, IAM
- AWS CLI configurado (`aws configure`)
- Docker instalado localmente
- Variables de entorno configuradas

## 🏗️ Paso 1: Configuración Inicial en AWS Console

### 1.1 Crear Repositorio ECR
1. Ve a **ECR** en AWS Console
2. **Create repository**
   - Name: `tensor-docling-worker`
   - Type: Private

### 1.2 Crear VPC y Subnets (si no tienes)
1. Ve a **VPC** en AWS Console
2. **Create VPC**
   - Name: `tensor-vpc`
   - IPv4 CIDR: `10.0.0.0/16`

3. **Create Subnets** (2 subnets públicas)
   - Subnet 1: `10.0.1.0/24` en zona `us-east-1a`
   - Subnet 2: `10.0.2.0/24` en zona `us-east-1b`

### 1.3 Crear Security Groups (🔒 Configuración de Seguridad CORRECTA)

**IMPORTANTE:** La configuración original con "from anywhere" es un **RIESGO DE SEGURIDAD CRÍTICO** y **NO FUNCIONA** con ElastiCache.

#### **Crear 3 Security Groups:**

1. **SG para ECS Tasks** (`tensor-ecs-sg`):
   - **Name:** `tensor-ecs-sg`
   - **VPC:** `tensor-vpc`
   - **Inbound rules:** ❌ **NINGUNA** (completamente cerrado)
   - **Outbound rules:** ✅ All traffic to `0.0.0.0/0`

2. **SG para Redis/ElastiCache** (`tensor-redis-sg`):
   - **Name:** `tensor-redis-sg`
   - **VPC:** `tensor-vpc`
   - **Inbound rules:**
     - TCP 6379 from `tensor-ecs-sg` (Security Group reference)
   - **Outbound rules:** ✅ All traffic

3. **SG para Qdrant** (`tensor-qdrant-sg`):
   - **Name:** `tensor-qdrant-sg`
   - **VPC:** `tensor-vpc`
   - **Inbound rules:**
     - TCP 6333 from `tensor-ecs-sg` (Security Group reference)
   - **Outbound rules:** ✅ All traffic

#### **¿Por qué esta arquitectura?**
- ✅ **Principio de menor privilegio**
- ✅ **Funciona con ElastiCache** (no acepta "from anywhere")
- ✅ **Seguridad máxima** (solo tráfico necesario)
- ✅ **Referencias entre SGs** (más mantenible)

### 1.4 Crear VPC Endpoints (🔒 Optimización de seguridad y costos)
1. Ve a **VPC > Endpoints**
2. **Create endpoint** para cada servicio:

   **ECR (Interface):**
   - Service: `com.amazonaws.us-east-1.ecr.api`
   - VPC: `tensor-vpc`
   - Subnets: tus 2 subnets
   - Security group: `tensor-ecs-sg`

   **ECR DKR (Interface):**
   - Service: `com.amazonaws.us-east-1.ecr.dkr`
   - VPC: `tensor-vpc`
   - Subnets: tus 2 subnets
   - Security group: `tensor-ecs-sg`

   **CloudWatch Logs (Interface):**
   - Service: `com.amazonaws.us-east-1.logs`
   - VPC: `tensor-vpc`
   - Subnets: tus 2 subnets
   - Security group: `tensor-ecs-sg`

   **S3 (Gateway - opcional):**
   - Service: `com.amazonaws.us-east-1.s3`
   - VPC: `tensor-vpc`
   - Route table: selecciona la route table de tu VPC

### 1.5 Crear Roles IAM
1. Ve a **IAM > Roles**
2. **Create role**
   - Trusted entity: `ECS`
   - Use case: `ECS Task`
   - Name: `ecsTaskExecutionRole`
   - Attach: `AmazonECSTaskExecutionRolePolicy`

## ⚠️ **Nota Importante: Configuración de Seguridad Optimizada**

✅ **Ya hemos actualizado tu configuración para:**
- Tareas completamente privadas (`assignPublicIp: "DISABLED"`)
- Sin necesidad de NAT Gateway
- Mejor seguridad y ahorro de costos

## 🚨 **SEGURIDAD CRÍTICA - Configuración de Security Groups**

### ❌ **ANTES (PELIGROSO):**
```bash
# ❌ NUNCA hagas esto
- Allow Redis (6379) from anywhere  # RIESGO CRÍTICO
- Allow Qdrant (6333) from anywhere # RIESGO CRÍTICO
```

### ✅ **AHORA (SEGURO):**
```bash
# ✅ Arquitectura correcta
tensor-ecs-sg:    Sin inbound rules (cerrado)
tensor-redis-sg:  TCP 6379 from tensor-ecs-sg
tensor-qdrant-sg: TCP 6333 from tensor-ecs-sg
```

### 🎯 **¿Por qué importa?**
- **ElastiCache NO funciona** con "from anywhere"
- **Riesgo de seguridad** exponiendo puertos al mundo
- **Principio de menor privilegio** violado
- **Referencias entre SGs** más mantenibles

## 🔧 Paso 2: Configuración de Variables

Edita `aws/task-definition.json` y reemplaza:

```json
{
  "YOUR_ACCOUNT_ID": "891376931243",
  "YOUR_REDIS_ENDPOINT": "tu-redis-endpoint.cache.amazonaws.com",
  "YOUR_QDRANT_ENDPOINT": "tu-qdrant-endpoint",
  "YOUR_DB_ENDPOINT": "tu-db-endpoint.rds.amazonaws.com",
  "YOUR_DB_USER": "tu-usuario-db",
  "YOUR_DB_PASSWORD": "tu-password-db",
  "YOUR_DB_NAME": "tu-db-name",
  "YOUR_SUPABASE_URL": "tu-supabase-url",
  "YOUR_SUPABASE_ANON_KEY": "tu-supabase-key"
}
```

## 🚀 Paso 3: Despliegue Automatizado

### 3.1 Hacer ejecutables los scripts
```bash
chmod +x aws/deploy.sh
chmod +x aws/vpc-endpoints-setup.sh
```

### 3.1.1 Configurar VPC Endpoints (Opcional - Automatizado)
Si prefieres automatizar la creación de VPC Endpoints:

```bash
# Edita el script con tus IDs
nano aws/vpc-endpoints-setup.sh

# Ejecuta el script
./aws/vpc-endpoints-setup.sh
```

### 3.2 Construir y subir imagen
```bash
./aws/deploy.sh build
```

### 3.3 Desplegar servicio
```bash
./aws/deploy.sh deploy
```

### 3.4 Configurar auto scaling
```bash
./aws/deploy.sh scale
```

## 📊 Monitoreo

### Ver logs del servicio
```bash
aws logs tail /ecs/tensor-docling-worker --follow --region us-east-1
```

### Ver estado del servicio
```bash
aws ecs describe-services --cluster tensor-cluster --services tensor-docling-service --region us-east-1
```

## 🔄 Actualizaciones

Para actualizar con cambios en el código:

```bash
# Reconstruir imagen
./aws/deploy.sh build

# Actualizar servicio
./aws/deploy.sh update
```

## 💰 Costos Estimados (Configuración Optimizada)

### ✅ **Con VPC Endpoints (Recomendado):**
- **ECS Fargate**: ~$0.05/hora por task (2 vCPU, 4GB RAM)
- **ECR**: ~$0.10/mes por repositorio
- **VPC Endpoints**: ~$0.015/hora por endpoint (4 endpoints)
- **CloudWatch Logs**: ~$1-2/mes
- **NAT Gateway**: ❌ **$0** (no necesitas)

**Total mensual (1 task continuo)**: ~$40/mes

### ❌ **Con NAT Gateway (tradicional):**
- **ECS Fargate**: ~$0.05/hora por task
- **NAT Gateway**: ~$36/mes + costos de data transfer
- **Data Transfer**: ~$0.045/GB

**Total mensual aproximado**: ~$80-100/mes

### 💡 **Ahorro con VPC Endpoints:**
- **~40-60% menos costos** en infraestructura
- **Mejor seguridad** (tareas completamente privadas)
- **Sin data transfer fees** para servicios AWS

## 🆘 Troubleshooting

### Problema: No puede conectar a Redis/Qdrant
- ✅ **Primero verifica Security Groups:**
  - `tensor-ecs-sg` debe tener outbound "All traffic"
  - `tensor-redis-sg` debe permitir TCP 6379 desde `tensor-ecs-sg`
  - `tensor-qdrant-sg` debe permitir TCP 6333 desde `tensor-ecs-sg`
- Verifica las variables de entorno en `task-definition.json`
- Asegúrate que Redis y Qdrant estén accesibles desde VPC

### Problema: "from anywhere" no funciona con ElastiCache
- **ElastiCache NO acepta conexiones** con "from anywhere" por seguridad
- **Solución:** Usa referencias entre Security Groups como se documenta arriba

### Problema: Tasks no pueden acceder a Internet/VPC Endpoints
- Verifica que `tensor-ecs-sg` tenga outbound rules correctas
- Confirma que VPC Endpoints estén asociados al `tensor-ecs-sg`

### Problema: Task falla al iniciar
```bash
aws ecs describe-tasks --cluster tensor-cluster --tasks <task-id> --region us-east-1
```

### Problema: Auto scaling no funciona
- Verifica que el rol IAM tenga permisos de auto scaling
- Revisa las métricas de CloudWatch

## 🎯 Próximos Pasos

1. **Configurar CloudWatch Alarms** para alertas
2. **Implementar CI/CD** con GitHub Actions
3. **Configurar múltiples entornos** (dev, staging, prod)
4. **Optimizar costos** con Fargate Spot

---

## 🛡️ **Recordatorio de Seguridad CRÍTICA**

**ANTES de desplegar, verifica que tus Security Groups estén configurados correctamente:**

- ❌ **NO uses** "from anywhere" (riesgo de seguridad)
- ✅ **SÍ usa** referencias entre Security Groups
- ✅ **SÍ usa** `tensor-ecs-sg` para tus tasks ECS
- ✅ **ElastiCache requiere** referencias específicas de SG

**¡Tu dev salvó el proyecto de un desastre de seguridad!** 🦸‍♂️

¡Tu worker de Docling ya está listo para escalar automáticamente con SEGURIDAD! 🔒🚀
