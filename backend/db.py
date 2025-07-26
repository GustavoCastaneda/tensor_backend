import os
import urllib.parse
from sqlmodel import SQLModel, create_engine, Session
from dotenv import load_dotenv

load_dotenv()  # carga las variables de .env

# URL de conexión de Supabase, sin query params
raw_db_url = os.getenv("SUPABASE_DB_URL")
if not raw_db_url:
    raise RuntimeError("SUPABASE_DB_URL no está definida en .env")

# Remover parámetros de consulta (e.g. prepare_threshold) de la URL
url_parts = urllib.parse.urlsplit(raw_db_url)
clean_db_url = urllib.parse.urlunsplit(
    (url_parts.scheme, url_parts.netloc, url_parts.path, "", "")
)

# Crear motor con umbral de statements preparadas desactivado
engine = create_engine(
    clean_db_url,
    echo=False,          # True para ver SQL en consola
    pool_size=10,
    max_overflow=20,
    connect_args={"prepare_threshold": 0},
)

def get_session() -> Session:
    """Dependencia de FastAPI para inyectar sesiones síncronas."""
    with Session(engine) as session:
        yield session

# Solo para desarrollo local; en producción confía en las migraciones
def init_db() -> None:
    SQLModel.metadata.create_all(engine)
