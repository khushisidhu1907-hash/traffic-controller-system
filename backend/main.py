"""FastAPI application entry point: ``uvicorn backend.main:app``."""
import logging
import json
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import create_tables
from backend.routes import analytics, assistant, controller, health, metrics, simulation

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


handler = logging.StreamHandler()
handler.setFormatter(JsonLogFormatter())
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), handlers=[handler])
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        create_tables()
        logger.info("Database tables are ready")
    except Exception:
        logger.exception("Database initialization failed")
        raise
    yield


app = FastAPI(
    title="Autonomous Traffic Management API",
    description=("Telemetry and fixed/adaptive signal-controller API for the Pygame traffic simulation. "
                 "Mode changes are acknowledged only after the simulator applies them at a safe phase boundary."),
    version="1.0.0",
    lifespan=lifespan,
)

origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(health.router)
app.include_router(controller.router)
app.include_router(simulation.router)
app.include_router(metrics.router)
app.include_router(analytics.router)
app.include_router(assistant.router)

# Same-origin hosting avoids broadening the API's existing CORS policy.
dashboard_dir = Path(__file__).resolve().parent.parent / "frontend"
if dashboard_dir.is_dir():
    app.mount("/dashboard", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")
