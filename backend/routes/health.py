"""Health and connectivity endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import ApiStatus
from backend.services.metrics_service import latest_received_at

router = APIRouter(tags=["health"])


def telemetry_is_fresh(last_update, freshness_seconds: int = 15) -> bool:
    if last_update is None:
        return False
    if last_update.tzinfo is None:
        last_update = last_update.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_update).total_seconds() <= freshness_seconds


@router.get("/")
def root():
    return {"project": "Autonomous AI-Based Traffic Management System", "status": "ok"}


@router.get("/api/health")
def health():
    return {"status": "healthy"}


@router.get("/api/status", response_model=ApiStatus)
def status(db: Session = Depends(get_db)):
    try:
        last_update = latest_received_at(db)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Status data is temporarily unavailable") from exc
    return {"status": "running", "simulation_connected": telemetry_is_fresh(last_update), "last_data_update": last_update}
