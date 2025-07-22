import os
from fastapi import Request, HTTPException, status, Depends
from jose import jwt
import httpx, cachetools, functools
from dotenv import load_dotenv

load_dotenv()

ISSUER = os.getenv("CLERK_ISSUER")
JWKS_URL = os.getenv("CLERK_JWKS_URL")

if not ISSUER or not JWKS_URL:
    raise RuntimeError("Faltan CLERK_ISSUER o CLERK_JWKS_URL en .env")

# -- JWKS cache 1 hora --
@cachetools.cached(cachetools.TTLCache(maxsize=1, ttl=60 * 60))
def get_jwks():
    resp = httpx.get(JWKS_URL, timeout=5)
    resp.raise_for_status()
    return resp.json()["keys"]

async def get_current_user(req: Request):
    """
    Devuelve los claims JWT ya verificados.
    Lanza 401 si falta o es inválido.
    """
    auth = req.headers.get("authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = auth.split()[1]

    try:
        payload = jwt.decode(
            token,
            get_jwks(),
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"verify_aud": False},  # Clerk no manda "aud" por defecto
        )
        return payload  # dict con 'sub', 'email', etc.
    except jwt.JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )
