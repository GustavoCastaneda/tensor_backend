# backend/main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes import users
from backend.routes import datasets
from backend.db import init_db            # crea tablas en dev, opcional

app = FastAPI(title="Tensor Workspace API")

# ──────────────── CORS ────────────────
origins = [
    "http://localhost:3000",                # Next.js dev
    "https://app.tu-dominio.com",           # dominio producción (ajústalo)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],                    # GET, POST, PUT, DELETE…
    allow_headers=["*"],                    # Authorization, Content-Type…
)

# ──────────────── Rutas ────────────────
app.include_router(users.router)
app.include_router(datasets.router)

# ──────────────── BD dev (opcional) ────────────────
if os.getenv("ENV") == "dev" and os.getenv("INIT_DB", "false") == "true":
    init_db()

# ──────────────── Comando `poetry run dev` ────────────────
def dev():
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=4000,
        reload=True,
    )
