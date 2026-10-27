"""Metric persistence and query operations."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.models import MetricSnapshot, SimulationSession
from backend.schemas import TrafficMetricsIn


def ensure_active_session(db: Session) -> SimulationSession:
    session = db.scalar(select(SimulationSession).where(SimulationSession.status == "active").order_by(desc(SimulationSession.id)))
    if session is None:
        session = SimulationSession()
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


def store_metrics(db: Session, metrics: TrafficMetricsIn) -> MetricSnapshot:
    session = ensure_active_session(db)
    snapshot = MetricSnapshot(
        session_id=session.id,
        simulation_timestamp=metrics.simulation_timestamp,
        received_at=datetime.now(timezone.utc),
        total_vehicles=metrics.total_vehicles,
        motorcycle_count=metrics.motorcycle_count,
        car_count=metrics.car_count,
        truck_count=metrics.truck_count,
        vehicles_passed=metrics.vehicles_passed,
        queues=metrics.queues.model_dump(),
        average_waiting_time=metrics.average_waiting_time,
        maximum_queue_length=metrics.maximum_queue_length,
        signal_phase=metrics.signal_phase,
        traffic_density=metrics.traffic_density,
        controller_mode=metrics.controller_mode,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def latest_metrics(db: Session) -> Optional[MetricSnapshot]:
    return db.scalar(select(MetricSnapshot).order_by(desc(MetricSnapshot.received_at), desc(MetricSnapshot.id)).limit(1))


def metric_history(db: Session, limit: int = 100, offset: int = 0) -> list[MetricSnapshot]:
    return list(db.scalars(select(MetricSnapshot).order_by(desc(MetricSnapshot.received_at), desc(MetricSnapshot.id)).offset(offset).limit(limit)))


def latest_received_at(db: Session) -> Optional[datetime]:
    latest = latest_metrics(db)
    return latest.received_at if latest else None
