"""Recorded-data analytics, performance availability, and CSV export."""
import csv
import io
import logging
from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ControlDecision, MetricSnapshot
from backend.schemas import AnalyticsSummary, ControlDecisionIn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummary)
def summary(db: Session = Depends(get_db)):
    try:
        rows = list(db.scalars(select(MetricSnapshot).order_by(MetricSnapshot.received_at)))
    except SQLAlchemyError as exc:
        logger.exception("Could not calculate analytics")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Analytics are temporarily unavailable") from exc
    if not rows:
        return {"snapshot_count": 0, "average_vehicles": 0, "peak_vehicles": 0,
                "average_waiting_time": 0, "maximum_observed_queue": 0,
                "vehicles_passed_latest": 0, "first_snapshot": None, "last_snapshot": None}
    return {
        "snapshot_count": len(rows),
        "average_vehicles": mean(row.total_vehicles for row in rows),
        "peak_vehicles": max(row.total_vehicles for row in rows),
        "average_waiting_time": mean(row.average_waiting_time for row in rows),
        "maximum_observed_queue": max(row.maximum_queue_length for row in rows),
        "vehicles_passed_latest": rows[-1].vehicles_passed,
        "first_snapshot": rows[0].simulation_timestamp,
        "last_snapshot": rows[-1].simulation_timestamp,
    }


@router.get("/performance")
def performance(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Report recorded measurements only; no synthetic runtime benchmark values."""
    try:
        count = db.query(MetricSnapshot).count()
        latest = db.scalar(select(MetricSnapshot).order_by(MetricSnapshot.received_at.desc()).limit(1))
    except SQLAlchemyError as exc:
        logger.exception("Could not read performance measurements")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Performance data is temporarily unavailable") from exc
    return {
        "measurement_source": "recorded traffic telemetry",
        "snapshot_count": count,
        "latest_snapshot_received_at": latest.received_at if latest else None,
        "available_measurements": ["snapshot_count", "latest_snapshot_received_at"] if count else [],
        "message": "No runtime latency or throughput measurements are collected yet." if not count else None,
    }


@router.get("/export")
def export_metrics(db: Session = Depends(get_db)):
    try:
        rows = list(db.scalars(select(MetricSnapshot).order_by(MetricSnapshot.received_at)))
    except SQLAlchemyError as exc:
        logger.exception("Could not export traffic metrics")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Metric export is temporarily unavailable") from exc
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "simulation_timestamp", "received_at", "total_vehicles",
                     "motorcycle_count", "car_count", "truck_count", "vehicles_passed",
                     "north_queue", "south_queue", "east_queue", "west_queue",
                     "average_waiting_time", "maximum_queue_length", "signal_phase", "traffic_density"])
    for row in rows:
        writer.writerow([row.id, row.simulation_timestamp.isoformat(), row.received_at.isoformat(),
                         row.total_vehicles, row.motorcycle_count, row.car_count, row.truck_count,
                         row.vehicles_passed, row.queues["north"], row.queues["south"],
                         row.queues["east"], row.queues["west"], row.average_waiting_time,
                         row.maximum_queue_length, row.signal_phase, row.traffic_density])
    return Response(content=buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=traffic_metrics.csv"})


@router.post("/decisions", status_code=201)
def record_decision(decision: ControlDecisionIn, db: Session = Depends(get_db)):
    """Persist future fixed/adaptive/ML controller decisions; no AI is implied."""
    item = ControlDecision(
        decided_at=decision.decided_at,
        selected_phase=decision.selected_phase,
        reason=decision.reason,
        observed_queues=decision.observed_queues.model_dump(),
        controller_mode=decision.controller_mode.value,
        green_duration=decision.green_duration,
    )
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Could not persist control decision")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Could not store control decision") from exc
    return {"id": item.id, "decided_at": item.decided_at, "controller_mode": item.controller_mode,
            "selected_phase": item.selected_phase}
