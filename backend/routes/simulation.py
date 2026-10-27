"""API-side simulation control endpoints."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import DensityRequest, SimulationControlRequest, SimulationStatus
from backend.services.metrics_service import latest_received_at
from backend.services.simulation_service import simulation_control
from backend.services.controller_service import controller_runtime

router = APIRouter(prefix="/api/simulation", tags=["simulation"])
logger = logging.getLogger(__name__)


@router.get("/status", response_model=SimulationStatus)
def get_simulation_status(db: Session = Depends(get_db)):
    try:
        last_update = latest_received_at(db)
    except SQLAlchemyError as exc:
        logger.exception("Could not retrieve simulation telemetry status")
        raise HTTPException(status_code=503, detail="Simulation status is temporarily unavailable") from exc
    heartbeat = controller_runtime.status()["updated_at"]
    connected = bool(heartbeat and (datetime.now(timezone.utc) - heartbeat).total_seconds() <= 5)
    return {**simulation_control.status(connected), "last_data_update": last_update}


def _require_connected_simulator():
    heartbeat = controller_runtime.status()["updated_at"]
    if not heartbeat or (datetime.now(timezone.utc) - heartbeat).total_seconds() > 5:
        raise HTTPException(status_code=409, detail="Start the Pygame simulation before sending controls")


@router.post("/start", status_code=202)
def start(request: SimulationControlRequest = SimulationControlRequest()):
    _require_connected_simulator()
    simulation_control.request("start", request.requested_by)
    return {"accepted": True, "message": "Resume requested; the running simulator will acknowledge it.", **simulation_control.status()}


@router.post("/pause", status_code=202)
def pause(request: SimulationControlRequest = SimulationControlRequest()):
    _require_connected_simulator()
    simulation_control.request("pause", request.requested_by)
    return {"accepted": True, "message": "Pause requested; the running simulator will acknowledge it.", **simulation_control.status()}


@router.post("/reset", status_code=202)
def reset(request: SimulationControlRequest = SimulationControlRequest()):
    _require_connected_simulator()
    simulation_control.request("reset", request.requested_by)
    return {"accepted": True, "message": "Vehicle and counter reset requested; the running simulator will acknowledge it.", **simulation_control.status()}


@router.post("/density", status_code=202)
def density(request: DensityRequest):
    _require_connected_simulator()
    simulation_control.set_density(request.density)
    return {"accepted": True, "message": "Density change requested; the running simulator will acknowledge it.", **simulation_control.status()}


@router.get("/command")
def get_simulation_command():
    """Polled by the simulator worker; Pygame state is only changed on its main thread."""
    return simulation_control.command()
