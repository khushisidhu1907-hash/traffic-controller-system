"""Live and historical traffic metric endpoints."""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import TrafficMetricsIn, TrafficMetricsOut
from backend.services.metrics_service import latest_metrics, metric_history, store_metrics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("", response_model=TrafficMetricsOut)
def get_latest_metrics(db: Session = Depends(get_db)):
    try:
        snapshot = latest_metrics(db)
    except SQLAlchemyError as exc:
        logger.exception("Could not retrieve latest traffic metrics")
        raise HTTPException(status_code=503, detail="Traffic metrics are temporarily unavailable") from exc
    if snapshot is None:
        raise HTTPException(status_code=503, detail="Traffic telemetry is not available yet")
    return snapshot


@router.post("", response_model=TrafficMetricsOut, status_code=201)
def post_metrics(metrics: TrafficMetricsIn, db: Session = Depends(get_db)):
    try:
        return store_metrics(db, metrics)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Could not persist traffic metrics")
        raise HTTPException(status_code=503, detail="Could not store traffic metrics") from exc


@router.get("/history", response_model=List[TrafficMetricsOut])
def get_metrics_history(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        return metric_history(db, limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        logger.exception("Could not retrieve traffic history")
        raise HTTPException(status_code=503, detail="Traffic history is temporarily unavailable") from exc
