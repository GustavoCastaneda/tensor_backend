-- Add workspace_id to datasets table
-- This enables workspace-based organization for datasets

-- Add workspace_id column to datasets table
ALTER TABLE public.datasets 
ADD COLUMN workspace_id TEXT;

-- Create index for workspace_id in datasets
CREATE INDEX idx_datasets_workspace_id ON public.datasets(workspace_id);

-- Update existing datasets to use user_id as workspace_id (temporary migration)
-- This creates a 1:1 mapping between user_id and workspace_id for existing data
UPDATE public.datasets 
SET workspace_id = user_id 
WHERE workspace_id IS NULL;

-- Make workspace_id NOT NULL after populating existing data
ALTER TABLE public.datasets 
ALTER COLUMN workspace_id SET NOT NULL;

-- Add foreign key constraint to datasets table
ALTER TABLE public.datasets 
ADD CONSTRAINT fk_datasets_workspace_id 
FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

-- Also add foreign key constraint to columns table through datasets
-- (columns already references datasets, so this ensures workspace consistency)
ALTER TABLE public.columns 
ADD CONSTRAINT fk_columns_workspace_id 
FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE CASCADE;
