# Formato de Citas - Chunking v2

## Estructura de Citas
Con el nuevo payload de Qdrant, las citas se construyen así:

**Formato**: `{title} · p.{page} · c.{chunk_seq}`

### Ejemplos:
- `Documento.pdf · p.12 · c.3` (Página 12, chunk 3)
- `Reporte_Financiero.pdf · p.5 · c.1` (Página 5, chunk 1)
- `Manual_Usuario.pdf · p.23 · c.2` (Página 23, chunk 2)

## Payload de Qdrant (Esquema Mínimo)

```json
{
  "workspace_id": "user_123",
  "doc_id": "doc_456",
  "title": "Documento.pdf",
  "page": 12,           // base 1
  "chunk_seq": 3,       // 1,2,3... (base 1)
  "block_type": "text", // "text" | "table" | "formula" | "figure"
  "text": "contenido del chunk...",
  
  // Campos opcionales
  "section_path": "H1 > H2 > H3",  // Jerarquía de encabezados
  "bbox_norm": [0.1, 0.2, 0.8, 0.3], // [x,y,w,h] 0-1 para resaltar
  "char_start": 150,    // Posición de inicio en página
  "char_end": 300,      // Posición de fin en página
  "hash": "a1b2c3d4...", // MD5 para deduplicar
  "lang": "en"          // Idioma
}
```

## Beneficios del Nuevo Formato

1. **Citas precisas**: `page` + `chunk_seq` para localización exacta
2. **Deduplicación**: `hash` para evitar chunks duplicados
3. **Resaltado visual**: `bbox_norm` para marcar área en el visor
4. **Contexto jerárquico**: `section_path` para navegación
5. **Multilenguaje**: `lang` para procesamiento específico
6. **Tipos de contenido**: `block_type` para diferentes tipos de elementos

