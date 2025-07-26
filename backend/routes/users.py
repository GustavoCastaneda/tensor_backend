from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.security import get_current_user
from backend.db import get_session          # ← sesión sync
from backend.models import User             # ← modelo SQLModel

router = APIRouter()

@router.get("/me")
def me(
    user = Depends(get_current_user),       # JWT verificado
    session: Session = Depends(get_session) # conexión Postgres
):
    # 1) ¿Ya existe en la tabla?
    db_user = session.get(User, user["sub"])

    # 2) Si no existe, lo creamos
    if db_user is None:
        db_user = User(
            id    = user["sub"],
            email = user.get("email_address") or user.get("email"),
        )
        session.add(db_user)
        session.commit()        # ← importante guardar
        session.refresh(db_user)

    # 3) Respondemos con los datos almacenados
    return {
        "id": db_user.id,
        "email": db_user.email,
    }
