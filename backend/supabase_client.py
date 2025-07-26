import os
from supabase import create_client, Client

def get_supabase() -> Client:
    url  = os.getenv("SUPABASE_URL")
    key  = os.getenv("SUPABASE_SERVICE_ROLE_KEY")   # clave con permisos Storage
    return create_client(url, key)
