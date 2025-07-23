# backend/db.py
import os
from sqlmodel import SQLModel, create_engine, Session
from dotenv import load_dotenv

load_dotenv()  # carga las variables de .env

DATABASE_URL = os.getenv("SUPABASE_DB_URL")
if DATABASE_URL is None:
    raise RuntimeError("SUPABASE_DB_URL no está definida en .env")

engine = create_engine(
    DATABASE_URL,
    echo=False,          # pon True si quieres ver SQL en consola
    pool_size=10,
    max_overflow=20,
)

def get_session() -> Session:
    """Dependencia de FastAPI para inyectar sesiones síncronas."""
    with Session(engine) as session:
        yield session

# Solo para desarrollo local; en producción confía en las migraciones
def init_db() -> None:
    SQLModel.metadata.create_all(engine)
