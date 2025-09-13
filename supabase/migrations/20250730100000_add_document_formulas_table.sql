-- Migración para agregar tabla de fórmulas de documentos
-- Fecha: 2025-07-30

-- Tabla de fórmulas matemáticas extraídas de documentos
create table public.document_formulas (
  id               uuid primary key default gen_random_uuid(),
  document_id      uuid references public.documents(id) on delete cascade,

  page_number      int     not null default 0,  -- página donde se encontró la fórmula (1-based)
  formula_index    int     not null default 0,  -- índice de la fórmula en la página (0-based)
  latex_code       text    not null,            -- código LaTeX de la fórmula
  original_text    text,                        -- texto original de la fórmula
  confidence_score float,                       -- confianza del reconocimiento (0-1)

  created_at       timestamptz default now()
);

-- Índices para búsquedas rápidas
create index document_formulas_document_id_idx on public.document_formulas(document_id);
create index document_formulas_page_number_idx on public.document_formulas(page_number, formula_index);

-- Comentarios para documentación
comment on table public.document_formulas is 'Fórmulas matemáticas extraídas de documentos usando Docling';
comment on column public.document_formulas.latex_code is 'Código LaTeX generado por el modelo de formula understanding de Docling';
comment on column public.document_formulas.confidence_score is 'Puntuación de confianza del reconocimiento (0-1)';
comment on column public.documents.formulas_count is 'Cantidad total de fórmulas matemáticas encontradas en el documento';

