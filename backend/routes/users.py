from fastapi import APIRouter, Depends
from backend.security import get_current_user

router = APIRouter()

@router.get("/me")
def me(user = Depends(get_current_user)):
    return {
        "id": user["sub"],
        "email": user.get("email_address") or user.get("email"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
    }
