-- Migración para agregar tabla de documentos y chunks
-- Fecha: 2025-07-30

-- Tabla de documentos (PDF/DOCX)
create table public.documents (
  id           uuid primary key default gen_random_uuid(),
  user_id      text    not null,
  filename     text    not null,
  storage_url  text    not null,
  status       text    not null default 'processing',  -- processing | ready_for_embeddings | ready_for_chat | error
  created_at   timestamptz default now(),
  pages_count  int     default 0,
  text_chars   int     default 0
);

-- Tabla de chunks de documentos
create table public.doc_chunks (
  id             uuid primary key default gen_random_uuid(),
  document_id    uuid references public.documents(id) on delete cascade,
  page_number    int     not null default 0,
  chunk_index    int     not null default 0,
  content        text    not null
);

-- Índices para búsquedas rápidas
create index documents_user_id_idx on public.documents(user_id);
create index documents_status_idx on public.documents(status);
create index doc_chunks_document_id_idx on public.doc_chunks(document_id);
create index doc_chunks_page_number_idx on public.doc_chunks(page_number, chunk_index);

-- Comentarios para documentación
comment on table public.documents is 'Documentos PDF/DOCX subidos por usuarios';
comment on table public.doc_chunks is 'Chunks de texto extraídos de documentos para embeddings';
comment on column public.documents.status is 'Estado del procesamiento: processing → ready_for_embeddings → ready_for_chat | error';

