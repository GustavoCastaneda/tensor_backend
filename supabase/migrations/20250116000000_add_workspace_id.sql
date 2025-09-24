-- Add workspace_id to documents and doc_chunks tables
-- This enables chunking v2 with workspace-based organization

-- Add workspace_id to documents table
ALTER TABLE documents 
ADD COLUMN workspace_id TEXT;

-- Create index for workspace_id in documents
CREATE INDEX idx_documents_workspace_id ON documents(workspace_id);

-- Add workspace_id to doc_chunks table  
ALTER TABLE doc_chunks 
ADD COLUMN workspace_id TEXT;

-- Create index for workspace_id in doc_chunks
CREATE INDEX idx_doc_chunks_workspace_id ON doc_chunks(workspace_id);

-- Update existing records to use user_id as workspace_id (temporary migration)
UPDATE documents 
SET workspace_id = user_id 
WHERE workspace_id IS NULL;

UPDATE doc_chunks 
SET workspace_id = (
    SELECT d.user_id 
    FROM documents d 
    WHERE d.id = doc_chunks.document_id
)
WHERE workspace_id IS NULL;

-- Make workspace_id NOT NULL after populating existing data
ALTER TABLE documents 
ALTER COLUMN workspace_id SET NOT NULL;

ALTER TABLE doc_chunks 
ALTER COLUMN workspace_id SET NOT NULL;

