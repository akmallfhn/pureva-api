import uvicorn

from app.core.config import settings


def dev():
    uvicorn.run("app.main:app", reload=True, host="0.0.0.0", port=settings.port)


def start():
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port)
