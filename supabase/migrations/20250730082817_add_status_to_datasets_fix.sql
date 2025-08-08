ALTER TABLE public.datasets
  ADD COLUMN IF NOT EXISTS status text
  DEFAULT 'processing'
  CHECK (
    status IN (
      'processing',
      'ready_for_embeddings',
      'ready_for_chat',
      'error'
    )
  );
  
