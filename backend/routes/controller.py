"""Adaptive/fixed controller control plane and simulator acknowledgement API."""
import logging
from datetime import datetime, timezone
from statistics import mean

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ControlDecision, MetricSnapshot
from backend.schemas import (
    ControllerCommand,
    ControllerDecisionPayload,
    ControllerModeRequest,
    ControllerStateReport,
    ControllerStatus,
)
from backend.services.controller_service import controller_runtime
from backend.services.simulation_service import simulation_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/controller", tags=["controller"])
SIMULATOR_HEARTBEAT_SECONDS = 5.0


def _status() -> dict:
    result = controller_runtime.status()
    updated = result["updated_at"]
    result["simulation_connected"] = bool(
        updated and (datetime.now(timezone.utc) - updated).total_seconds() <= SIMULATOR_HEARTBEAT_SECONDS
    )
    return result


@router.get("/status", response_model=ControllerStatus)
def get_controller_status():
    return _status()


@router.post("/mode", response_model=ControllerStatus, status_code=202)
def request_controller_mode(request: ControllerModeRequest):
    controller_runtime.request_mode(request.mode.value)
    return _status()


@router.get("/decision", response_model=ControllerDecisionPayload)
def get_latest_controller_decision():
    decision = controller_runtime.status()["last_decision"]
    if decision is None:
        raise HTTPException(status_code=404, detail="The simulator has not reported a controller decision yet")
    return decision


@router.get("/performance")
def get_controller_performance(db: Session = Depends(get_db)):
    """Compare actual telemetry recorded while each mode was active."""
    try:
        snapshots = list(db.scalars(
            select(MetricSnapshot).where(MetricSnapshot.controller_mode.is_not(None))
            .order_by(MetricSnapshot.received_at)
        ))
    except SQLAlchemyError as exc:
        logger.exception("Could not calculate controller performance")
        raise HTTPException(status_code=503, detail="Controller performance is temporarily unavailable") from exc
    grouped = {mode: [] for mode in ("fixed", "adaptive")}
    for snapshot in snapshots:
        if snapshot.controller_mode in grouped:
            grouped[snapshot.controller_mode].append(snapshot)
    modes = {}
    for mode, rows in grouped.items():
        if not rows:
            modes[mode] = {"snapshot_count": 0, "available_measurements": []}
            continue
        modes[mode] = {
            "snapshot_count": len(rows),
            "average_queue_length_per_approach": mean(sum(row.queues.values()) / 4 for row in rows),
            "average_waiting_time_seconds": mean(row.average_waiting_time for row in rows),
            "maximum_observed_queue": max(row.maximum_queue_length for row in rows),
            "latest_vehicles_passed_counter": rows[-1].vehicles_passed,
            "first_snapshot": rows[0].simulation_timestamp,
            "last_snapshot": rows[-1].simulation_timestamp,
            "available_measurements": [
                "average_queue_length_per_approach", "average_waiting_time_seconds",
                "maximum_observed_queue", "latest_vehicles_passed_counter",
            ],
        }
    return {
        "measurement_source": "real simulator telemetry tagged with simulator-acknowledged active mode",
        "modes": modes,
        "note": "The vehicles-passed value is the simulator's cumulative counter at the last sample, not a per-mode throughput total. Compare runs with similar demand and duration.",
    }


@router.get("/command", response_model=ControllerCommand)
def get_simulator_command():
    """Polled by the simulator worker; no Pygame state is changed here."""
    return controller_runtime.command()


@router.post("/state", response_model=ControllerStatus)
def report_simulator_state(report: ControllerStateReport, db: Session = Depends(get_db)):
    """Accept main-loop acknowledgements and decisions produced from live vehicles."""
    state = report.model_dump(mode="json")
    try:
        controller_runtime.acknowledge(state)
        simulation_control.acknowledge(state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    decision = report.decision
    if decision is not None:
        item = ControlDecision(
            decided_at=decision.decided_at,
            selected_phase=decision.selected_phase,
            reason=decision.reason,
            observed_queues=decision.observed_queues.model_dump(),
            observed_metrics=decision.observed_metrics,
            controller_mode=decision.controller_mode.value,
            green_duration=decision.green_duration,
        )
        try:
            db.add(item)
            db.commit()
            db.refresh(item)
        except SQLAlchemyError as exc:
            db.rollback()
            logger.exception("Could not persist simulator controller decision")
            raise HTTPException(status_code=503, detail="Controller decision could not be stored") from exc
    return _status()
