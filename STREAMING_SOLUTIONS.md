# Soluciones de Streaming Implementadas

## Problema Original
Error: `ResponseStream object has no attribute 'wait'`

## Solución 1: Corrección del Streaming Actual (OpenAI Responses API)

### Cambios realizados:
- ✅ Corregido `client.responses.stream()` → `client.responses.create(..., stream=True)`
- ✅ Eliminado `stream.wait()` y `stream.get_final_response()` (métodos inexistentes)
- ✅ Implementada acumulación manual de tokens durante el streaming
- ✅ Agregado manejo del evento `response.completed`

### Código corregido:
```python
# Antes (INCORRECTO):
with client.responses.stream(**request_kwargs) as stream:
    for event in stream:
        # ... procesar eventos
    stream.wait()  # ❌ Método no existe
    final_resp = stream.get_final_response()  # ❌ Método no existe

# Después (CORRECTO):
stream = client.responses.create(**request_kwargs, stream=True)
answer_text = ""
reasoning_text = ""

for event in stream:
    if event.type == "response.output_text.delta":
        answer_text += event.delta
        yield _sse_event({"type": "token", "text": event.delta})
    elif event.type == "response.completed":
        break
```

### Endpoint:
- `POST /chat/semantic` (existente, corregido)

## Solución 2: AI SDK Compatible (ai-datastream)

### Nuevas dependencias:
```toml
ai-datastream = "^0.0.1"
```

### Nuevo endpoint:
- `POST /chat/ai-chat`

### Características:
- ✅ Compatible con Vercel AI SDK frontend
- ✅ Usa `ai-datastream` para emitir streams estándar
- ✅ Manejo automático de tokens y eventos
- ✅ Integración con la lógica de RAG existente

### Uso desde el frontend:
```javascript
// Con AI SDK
import { useChat } from 'ai/react'

const { messages, input, handleInputChange, handleSubmit } = useChat({
  api: '/chat/ai-chat',
  body: {
    workspace_id: 'your-workspace-id'
  }
})
```

### Estructura de request:
```json
{
  "messages": [
    {"role": "user", "content": "¿Cuál es el tema principal del documento?"}
  ],
  "workspace_id": "workspace-uuid"
}
```

## Comparación de Soluciones

| Aspecto | Solución 1 (Corrección) | Solución 2 (AI SDK) |
|---------|-------------------------|---------------------|
| **Complejidad** | Baja | Media |
| **Compatibilidad** | Backend existente | Frontend AI SDK |
| **Mantenimiento** | Manual | Automático |
| **Funcionalidades** | Básico | Avanzado |
| **Migración** | Mínima | Nueva implementación |

## Recomendaciones

### Para uso inmediato:
- **Usar Solución 1**: Corrige el error actual sin cambios mayores

### Para nuevo desarrollo:
- **Usar Solución 2**: Mejor integración con frontend moderno

### Para migración gradual:
1. Implementar Solución 1 (ya hecho)
2. Probar Solución 2 en paralelo
3. Migrar frontend a AI SDK
4. Deprecar endpoint antiguo

## Instalación

```bash
# Instalar nueva dependencia
poetry install

# O con pip
pip install ai-datastream
```

## Testing

### Probar Solución 1:
```bash
curl -X POST "http://localhost:8000/chat/semantic" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "¿Qué contiene el documento?",
    "workspace_id": "your-workspace-id",
    "stream": true
  }'
```

### Probar Solución 2:
```bash
curl -X POST "http://localhost:8000/chat/ai-chat" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "¿Qué contiene el documento?"}],
    "workspace_id": "your-workspace-id"
  }'
```
