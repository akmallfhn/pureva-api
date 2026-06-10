import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhooks.whatsapp import router as whatsapp_router
from app.db.database import database_exists
from app.db.seed import seed_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-seed SQLite dari CSV saat pertama kali dijalankan.
    if not database_exists():
        logger.info("Database belum ada, menjalankan seed dari CSV...")
        result = seed_all()
        logger.info(f"Seed selesai: {result}")
    yield


app = FastAPI(title="Pureva WhatsApp Agent", version="0.1.0", lifespan=lifespan)
app.include_router(whatsapp_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
