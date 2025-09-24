-- Create workspaces table
-- This table manages workspace entities for organizing documents and data

CREATE TABLE public.workspaces (
    id TEXT PRIMARY KEY,  -- workspace_id (string identifier)
    owner_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX idx_workspaces_owner_user_id ON public.workspaces(owner_user_id);
CREATE INDEX idx_workspaces_name ON public.workspaces(name);

-- Add foreign key constraints to existing tables
-- Note: This assumes documents and doc_chunks already have workspace_id columns

-- Add foreign key constraint to documents table
ALTER TABLE public.documents 
ADD CONSTRAINT fk_documents_workspace_id 
FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

-- Add foreign key constraint to doc_chunks table  
ALTER TABLE public.doc_chunks 
ADD CONSTRAINT fk_doc_chunks_workspace_id 
FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

-- Add foreign key constraint to memory_units table
ALTER TABLE public.memory_units 
ADD CONSTRAINT fk_memory_units_workspace_id 
FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

-- Create a function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create trigger to automatically update updated_at
CREATE TRIGGER update_workspaces_updated_at 
    BEFORE UPDATE ON public.workspaces 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- Insert default workspace for existing users
-- This creates a workspace for each unique user_id in documents table
INSERT INTO public.workspaces (id, owner_user_id, name, description)
SELECT DISTINCT 
    user_id as id,  -- Use user_id as workspace_id for existing data
    user_id as owner_user_id,
    'Default Workspace' as name,
    'Auto-created workspace for existing user' as description
FROM public.documents 
WHERE user_id IS NOT NULL
ON CONFLICT (id) DO NOTHING;

-- Update any orphaned workspace_id references to point to default workspaces
-- This handles cases where workspace_id exists but workspace doesn't
UPDATE public.documents 
SET workspace_id = user_id 
WHERE workspace_id NOT IN (SELECT id FROM public.workspaces)
AND user_id IS NOT NULL;

UPDATE public.doc_chunks 
SET workspace_id = (
    SELECT d.user_id 
    FROM public.documents d 
    WHERE d.id = doc_chunks.document_id
)
WHERE workspace_id NOT IN (SELECT id FROM public.workspaces);

UPDATE public.memory_units 
SET workspace_id = (
    SELECT DISTINCT d.user_id 
    FROM public.documents d 
    WHERE d.workspace_id = memory_units.workspace_id
    LIMIT 1
)
WHERE workspace_id NOT IN (SELECT id FROM public.workspaces)
AND workspace_id IS NOT NULL;
