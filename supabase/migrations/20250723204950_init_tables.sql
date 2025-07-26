-- habilita pgcrypto para gen_random_uuid() si aún no existe
create extension if not exists pgcrypto;

-- tabla principal por archivo
create table public.datasets (
  id           uuid primary key default gen_random_uuid(),
  user_id      text    not null,
  filename     text,
  storage_url  text,
  parquet_url  text,
  status       text    not null default 'processing',  -- processing | ready | error
  rows_count   int,
  created_at   timestamptz default now()
);

-- columnas detectadas por archivo
create table public.columns (
  id             uuid primary key default gen_random_uuid(),
  dataset_id     uuid references public.datasets(id) on delete cascade,
  original_name  text    not null,
  canonical_name text,
  detected_type  text,                   -- number | text | date | bool | category | …
  sample_values  jsonb,
  description    text
);

-- índice rápido para buscar columnas por nombre
create index columns_dataset_id_idx on public.columns(dataset_id);
