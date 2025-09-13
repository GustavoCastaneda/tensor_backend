# backend/main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routes import chat
from backend.routes import users
from backend.routes import datasets
from backend.db import init_db            # crea tablas en dev, opcional
from backend.routes import documents

app = FastAPI(title="Tensor Workspace API")

# ──────────────── CORS ────────────────
origins = [
    "http://localhost:3000",                # Next.js dev
    "http://localhost:3001",                # React dev
    "http://localhost:3002",                # Vite dev
    "http://localhost:5173",                # Vite dev
    "http://localhost:8080",                # Vue dev
    "http://localhost:5000",                # Flask dev
    "https://app.tu-dominio.com",           # dominio producción (ajústalo)
    "http://127.0.0.1:3000",               # localhost alternativo
    "http://127.0.0.1:3001",               # localhost alternativo
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
app.include_router(documents.router)
app.include_router(chat.router)

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
