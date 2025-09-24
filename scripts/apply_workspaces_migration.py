#!/usr/bin/env python3
"""
Script para aplicar la migración de workspaces a la base de datos de producción
"""
import os
from sqlalchemy import create_engine, text

# Configurar la URL de la base de datos
DB_URL = os.getenv("SUPABASE_DB_URL")
if not DB_URL:
    raise SystemExit("SUPABASE_DB_URL no definido")

print(f"Conectando a la base de datos...")
engine = create_engine(DB_URL)

# Leer el archivo de migración
migration_file = "supabase/migrations/20250117000000_create_workspaces_table.sql"
print(f"Leyendo migración: {migration_file}")

with open(migration_file, 'r') as f:
    migration_sql = f.read()

print("Aplicando migración...")
try:
    with engine.begin() as conn:
        # Ejecutar la migración
        conn.execute(text(migration_sql))
    print("✅ Migración aplicada exitosamente!")
    
    # Verificar que la tabla se creó
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'workspaces'
        """))
        
        if result.fetchone():
            print("✅ Tabla 'workspaces' creada correctamente")
        else:
            print("❌ Error: Tabla 'workspaces' no encontrada")
            
        # Verificar registros
        result = conn.execute(text("SELECT COUNT(*) FROM workspaces"))
        count = result.scalar()
        print(f"📊 Registros en workspaces: {count}")
        
except Exception as e:
    print(f"❌ Error aplicando migración: {e}")
    raise
