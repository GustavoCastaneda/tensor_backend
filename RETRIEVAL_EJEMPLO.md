# Estrategia de Retrieval Adaptativa - Ejemplo de Uso

## Configuración

```python
from backend.retrieval_strategy import AdaptiveRetrievalStrategy
from qdrant_client import QdrantClient

# Configurar estrategia adaptativa
strategy = AdaptiveRetrievalStrategy(
    max_evidence_chars=2000  # Máx 2000 chars por bloque
)

# Cliente Qdrant
qdrant = QdrantClient(url="http://qdrant:6333")
```

## Tipos de Consulta y Límites Adaptativos

| Tipo | Top-K | Chunks/Página | Chunks/Doc | Uso |
|------|-------|---------------|------------|-----|
| **Resumen** | 20 | 5 | 10 | "Resumen del documento" |
| **Análisis** | 16 | 4 | 8 | "Analiza las tendencias" |
| **Específico** | 8 | 2 | 4 | "¿Cuál es el ROI?" |
| **Búsqueda** | 15 | 3 | 6 | "Métricas de rendimiento" |

## Ejemplos de Consultas Adaptativas

### 1. Consulta de Resumen (Límites Altos)
```python
query = "Resumen del documento de finanzas"
# Tipo: "resumen" → Top-K: 20, Chunks/Página: 5, Chunks/Doc: 10

evidence_blocks = strategy.retrieve_and_merge(
    query=query,
    workspace_id="user_123",
    qdrant_client=qdrant,
    collection_name="doc_456"
)
```

### 2. Consulta Específica (Límites Bajos)
```python
query = "¿Cuál es el ROI del proyecto?"
# Tipo: "específico" → Top-K: 8, Chunks/Página: 2, Chunks/Doc: 4

evidence_blocks = strategy.retrieve_and_merge(
    query=query,
    workspace_id="user_123",
    qdrant_client=qdrant,
    collection_name="doc_456"
)
```

### 3. Consulta de Análisis (Límites Medios)
```python
query = "Analiza las tendencias de crecimiento"
# Tipo: "análisis" → Top-K: 16, Chunks/Página: 4, Chunks/Doc: 8

evidence_blocks = strategy.retrieve_and_merge(
    query=query,
    workspace_id="user_123",
    qdrant_client=qdrant,
    collection_name="doc_456"
)
```

## Ejemplos de Salida Adaptativa

### Consulta Específica: "¿Cuál es el ROI?"
```
[Retrieval] Tipo de consulta: específico
[Retrieval] Límites: {'top_k_initial': 8, 'max_chunks_per_page': 2, 'max_chunks_per_doc': 4}
[Retrieval] Chunks finales: 3

Cita: Reporte_Financiero.pdf · p.12 · c.2
Texto: El ROI del proyecto es del 15% según el análisis...
Chars: 856
```

### Consulta de Resumen: "Resumen del documento"
```
[Retrieval] Tipo de consulta: resumen
[Retrieval] Límites: {'top_k_initial': 20, 'max_chunks_per_page': 5, 'max_chunks_per_doc': 10}
[Retrieval] Chunks finales: 8

Cita: Reporte_Financiero.pdf · p.12 · c.2-4
Texto: Las métricas de rendimiento incluyen ROI del 15%, 
margen de beneficio del 8.5%, y crecimiento anual del 12%...
Chars: 1847

Cita: Manual_Sistema.pdf · p.5 · c.1-2
Texto: El sistema de monitoreo registra métricas en tiempo real...
Chars: 1203
```

## Beneficios de la Estrategia Adaptativa

1. **Límites Dinámicos**: Se ajustan según el tipo de consulta
2. **Clasificación Automática**: Detecta resumen, análisis, específico, búsqueda
3. **Anti-colapso Adaptativo**: 2-5 chunks por página según consulta
4. **Balance Adaptativo**: 4-10 chunks por documento según consulta
5. **Fusión Inteligente**: Chunks contiguos se fusionan automáticamente
6. **Citas Precisas**: Formato consistente para el visor
7. **Re-rank Inteligente**: Prioriza tipos de contenido relevantes
8. **Escalabilidad**: Funciona con documentos grandes y pequeños
