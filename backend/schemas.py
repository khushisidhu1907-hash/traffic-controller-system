"""Validated API request and response models."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Direction(str, Enum):
    north = "north"
    south = "south"
    east = "east"
    west = "west"


class QueueLengths(BaseModel):
    north: int = Field(ge=0)
    south: int = Field(ge=0)
    east: int = Field(ge=0)
    west: int = Field(ge=0)


class TrafficMetricsIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "simulation_timestamp": "2026-10-18T12:00:00Z",
        "total_vehicles": 12, "motorcycle_count": 2, "car_count": 8,
        "truck_count": 2, "vehicles_passed": 45,
        "queues": {"north": 3, "south": 2, "east": 1, "west": 0},
        "average_waiting_time": 4.2, "maximum_queue_length": 3,
        "signal_phase": "NS", "traffic_density": 5
    }]})

    simulation_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_vehicles: int = Field(ge=0)
    motorcycle_count: int = Field(ge=0)
    car_count: int = Field(ge=0)
    truck_count: int = Field(ge=0)
    vehicles_passed: int = Field(ge=0)
    queues: QueueLengths
    average_waiting_time: float = Field(ge=0, allow_inf_nan=False)
    maximum_queue_length: int = Field(ge=0)
    signal_phase: str = Field(min_length=1, max_length=32)
    traffic_density: int = Field(ge=1, le=10)
    controller_mode: Optional[Literal["fixed", "adaptive"]] = None

    @field_validator("simulation_timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            # SQLite may return a naive datetime for a UTC value; interpret
            # timezone-less submissions consistently as UTC as well.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_vehicle_total(self):
        if self.motorcycle_count + self.car_count + self.truck_count != self.total_vehicles:
            raise ValueError("vehicle category counts must sum to total_vehicles")
        if self.maximum_queue_length < max(self.queues.model_dump().values()):
            raise ValueError("maximum_queue_length cannot be less than a directional queue")
        return self


class TrafficMetricsOut(TrafficMetricsIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    received_at: datetime


class SimulationControlRequest(BaseModel):
    """Empty-body-friendly control request; comments are useful for dashboards."""
    requested_by: str = Field(default="api", max_length=80)


class DensityRequest(BaseModel):
    density: int = Field(ge=1, le=10)


class TrafficAssistantRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    action: Optional[Literal[
        "analyze_traffic",
        "check_congestion",
        "optimize_signals",
        "predict_traffic",
        "emergency_mode",
    ]] = None

    @field_validator("message")
    @classmethod
    def require_nonblank_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class SimulationStatus(BaseModel):
    running: bool
    paused: bool
    density: int = Field(ge=1, le=10)
    requested_paused: bool
    requested_density: int = Field(ge=1, le=10)
    pending: bool
    command_revision: int = Field(ge=0)
    applied_revision: int = Field(ge=0)
    reset_revision: int = Field(ge=0)
    applied_reset_revision: int = Field(ge=0)
    control_mode: str
    control_state_is_api_side_only: bool
    telemetry_connected: bool
    last_data_update: Optional[datetime] = None


class ControlMode(str, Enum):
    fixed = "fixed"
    adaptive = "adaptive"
    ml = "ml"


class ControllerMode(str, Enum):
    fixed = "fixed"
    adaptive = "adaptive"


class ControllerModeRequest(BaseModel):
    mode: ControllerMode


class ControllerDecisionPayload(BaseModel):
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    selected_phase: Literal["NS", "EW"]
    reason: str = Field(min_length=1, max_length=500)
    observed_queues: QueueLengths
    observed_metrics: Dict[str, Any] = Field(default_factory=dict)
    controller_mode: ControllerMode
    green_duration: float = Field(gt=0, allow_inf_nan=False)


class ControllerCommand(BaseModel):
    requested_mode: ControllerMode
    revision: int = Field(ge=0)


class ControllerStateReport(BaseModel):
    active_mode: ControllerMode
    phase: str = Field(min_length=1, max_length=32)
    green_duration: float = Field(gt=0, allow_inf_nan=False)
    phase_duration_seconds: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    phase_elapsed_seconds: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    next_phase_preview: Optional[str] = Field(default=None, min_length=1, max_length=32)
    next_phase_in_seconds: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    command_revision: int = Field(ge=0)
    simulation_id: str = Field(min_length=1, max_length=80)
    simulation_control_revision: int = Field(default=0, ge=0)
    paused: bool = False
    density: int = Field(default=3, ge=1, le=10)
    reset_revision: int = Field(default=0, ge=0)
    decision: Optional[ControllerDecisionPayload] = None

    @model_validator(mode="after")
    def decision_matches_active_mode(self):
        if self.decision is not None and self.decision.controller_mode != self.active_mode:
            raise ValueError("decision controller_mode must match the simulator's active_mode")
        if (self.phase_duration_seconds is None) != (self.phase_elapsed_seconds is None):
            raise ValueError("phase duration and elapsed time must be reported together")
        if (self.next_phase_preview is None) != (self.next_phase_in_seconds is None):
            raise ValueError("next phase preview and transition time must be reported together")
        return self


class ControllerStatus(BaseModel):
    requested_mode: ControllerMode
    active_mode: Optional[ControllerMode] = None
    pending: bool
    command_revision: int
    applied_revision: int
    simulation_id: Optional[str] = None
    phase: Optional[str] = None
    green_duration: Optional[float] = None
    phase_duration_seconds: Optional[float] = None
    phase_elapsed_seconds: Optional[float] = None
    next_phase_preview: Optional[str] = None
    next_phase_in_seconds: Optional[float] = None
    previous_phase: Optional[str] = None
    paused: bool = False
    updated_at: Optional[datetime] = None
    simulation_connected: bool
    last_decision: Optional[ControllerDecisionPayload] = None




class ControlDecisionIn(BaseModel):
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    selected_phase: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=500)
    observed_queues: QueueLengths
    controller_mode: ControlMode
    green_duration: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)


class ApiStatus(BaseModel):
    status: str
    simulation_connected: bool
    last_data_update: Optional[datetime] = None


class AnalyticsSummary(BaseModel):
    snapshot_count: int
    average_vehicles: float
    peak_vehicles: int
    average_waiting_time: float
    maximum_observed_queue: int
    vehicles_passed_latest: int
    first_snapshot: Optional[datetime] = None
    last_snapshot: Optional[datetime] = None
