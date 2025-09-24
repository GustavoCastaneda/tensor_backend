import os
from sqlalchemy import create_engine, text

DB_URL = os.getenv("SUPABASE_DB_URL")
if not DB_URL:
    raise SystemExit("SUPABASE_DB_URL no definido")

engine = create_engine(DB_URL)

SQL = """
-- Documents
ALTER TABLE documents ADD COLUMN IF NOT EXISTS workspace_id TEXT;
UPDATE documents SET workspace_id = user_id WHERE workspace_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_documents_workspace_id ON documents(workspace_id);
-- intentar set not null si no hay nulos
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='documents' AND column_name='workspace_id' AND is_nullable='NO'
    ) THEN
        ALTER TABLE documents ALTER COLUMN workspace_id SET NOT NULL;
    END IF;
END $$;

-- Doc chunks
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS workspace_id TEXT;
UPDATE doc_chunks dc SET workspace_id = (
    SELECT d.workspace_id FROM documents d WHERE d.id = dc.document_id
) WHERE dc.workspace_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_doc_chunks_workspace_id ON doc_chunks(workspace_id);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='doc_chunks' AND column_name='workspace_id' AND is_nullable='NO'
    ) THEN
        ALTER TABLE doc_chunks ALTER COLUMN workspace_id SET NOT NULL;
    END IF;
END $$;
"""

with engine.begin() as conn:
    conn.execute(text(SQL))
print("Applied workspace_id columns and indexes (idempotent).")





