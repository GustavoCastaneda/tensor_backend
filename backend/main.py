from fastapi import FastAPI
from backend.routes import users

app = FastAPI(title="Tensor Workspace API")

app.include_router(users.router)

def dev():
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=4000, reload=True)
