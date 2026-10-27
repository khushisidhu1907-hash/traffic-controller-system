"""SQLAlchemy persistence models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SimulationSession(Base):
    __tablename__ = "simulation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    snapshots: Mapped[list["MetricSnapshot"]] = relationship(back_populates="session")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("simulation_sessions.id"), nullable=True)
    simulation_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    total_vehicles: Mapped[int] = mapped_column(Integer, nullable=False)
    motorcycle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    car_count: Mapped[int] = mapped_column(Integer, nullable=False)
    truck_count: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicles_passed: Mapped[int] = mapped_column(Integer, nullable=False)
    queues: Mapped[dict] = mapped_column(JSON, nullable=False)
    average_waiting_time: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_queue_length: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    traffic_density: Mapped[int] = mapped_column(Integer, nullable=False)
    controller_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    session: Mapped[Optional[SimulationSession]] = relationship(back_populates="snapshots")


class ControlDecision(Base):
    __tablename__ = "control_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    selected_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_queues: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    controller_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    green_duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
