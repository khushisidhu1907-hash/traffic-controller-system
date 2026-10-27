"""Explainable adaptive signal decisions; this module never changes live lights."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import os
from typing import Any, Mapping


AXES = ("NS", "EW")
DIRECTIONS = ("north", "south", "east", "west")


def clearance_transition(
    phase: str,
    elapsed_seconds: float,
    green_seconds: float,
    yellow_seconds: float,
    all_red_seconds: float,
) -> str | None:
    """Return the next clearance state; GREEN_BOUNDARY needs a controller choice."""
    if phase in AXES and elapsed_seconds >= green_seconds:
        return f"{phase}_YELLOW"
    if phase in ("NS_YELLOW", "EW_YELLOW") and elapsed_seconds >= yellow_seconds:
        return "ALL_RED"
    if phase == "ALL_RED" and elapsed_seconds >= all_red_seconds:
        return "GREEN_BOUNDARY"
    return None


def next_green_preview(
    phase: str,
    elapsed_seconds: float,
    green_seconds: float,
    yellow_seconds: float,
    all_red_seconds: float,
    last_green_phase: str,
    controller_mode: str,
) -> tuple[str, float]:
    """Describe the next green phase without advancing the live controller."""
    if phase in AXES:
        seconds = green_seconds - elapsed_seconds + yellow_seconds + all_red_seconds
    elif phase in ("NS_YELLOW", "EW_YELLOW"):
        seconds = yellow_seconds - elapsed_seconds + all_red_seconds
    elif phase == "ALL_RED":
        seconds = all_red_seconds - elapsed_seconds
    else:
        raise ValueError(f"unsupported signal phase: {phase}")

    if controller_mode == "adaptive":
        preview = "ADAPTIVE_SELECTION"
    elif controller_mode == "fixed":
        preview = "EW" if last_green_phase == "NS" else "NS"
    else:
        raise ValueError(f"unsupported controller mode: {controller_mode}")
    return preview, max(0.0, seconds)


def permitted_green_axis(phase: str) -> str | None:
    """Return at most one active green axis; clearance states are all-stop."""
    return phase if phase in AXES else None


@dataclass(frozen=True)
class SignalDecision:
    decided_at: str
    selected_phase: str
    green_duration: float
    observed_queues: dict[str, int]
    observed_metrics: dict[str, Any]
    reason: str
    controller_mode: str = "adaptive"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdaptiveSignalController:
    """Choose an axis and bounded green interval from a fresh simulation snapshot."""

    def __init__(
        self,
        min_green_seconds: float | None = None,
        max_green_seconds: float | None = None,
        max_red_seconds: float | None = None,
        fixed_green_seconds: float = 8.0,
        telemetry_max_age_seconds: float = 5.0,
    ) -> None:
        self.min_green_seconds = float(min_green_seconds or os.getenv("TRAFFIC_MIN_GREEN_SECONDS", "5"))
        self.max_green_seconds = float(max_green_seconds or os.getenv("TRAFFIC_MAX_GREEN_SECONDS", "30"))
        self.max_red_seconds = float(max_red_seconds or os.getenv("TRAFFIC_MAX_RED_SECONDS", "45"))
        self.fixed_green_seconds = float(fixed_green_seconds)
        self.telemetry_max_age_seconds = float(telemetry_max_age_seconds)
        if not (0 < self.min_green_seconds <= self.max_green_seconds):
            raise ValueError("green duration limits must satisfy 0 < minimum <= maximum")
        if self.max_red_seconds <= 0 or self.telemetry_max_age_seconds <= 0:
            raise ValueError("red and telemetry age limits must be positive")

    def decide(
        self,
        metrics: Mapping[str, Any] | None,
        current_phase: str,
        time_since_green: Mapping[str, float] | None = None,
    ) -> SignalDecision:
        """Return a safe next-green decision, falling back to fixed alternation."""
        current = current_phase if current_phase in AXES else "NS"
        fallback = "EW" if current == "NS" else "NS"
        now = datetime.now(timezone.utc)
        try:
            queues, observed = self._validate_snapshot(metrics, now)
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            return SignalDecision(
                decided_at=now.isoformat(), selected_phase=fallback,
                green_duration=self._bounded_fixed_duration(),
                observed_queues={direction: 0 for direction in DIRECTIONS},
                observed_metrics={},
                reason=f"Safe fixed-cycle fallback: invalid or stale telemetry ({exc}).",
            )

        waits = time_since_green or {}
        red_due = [axis for axis in AXES if float(waits.get(axis, 0.0)) >= self.max_red_seconds]
        if red_due:
            selected = max(red_due, key=lambda axis: float(waits.get(axis, 0.0)))
            reason = f"Fairness override: {selected} reached the maximum red-time limit."
            duration = self.min_green_seconds
        else:
            ns_score = self._axis_score("NS", queues, observed)
            ew_score = self._axis_score("EW", queues, observed)
            if math.isclose(ns_score, ew_score, abs_tol=1e-9):
                selected = fallback
                reason = "Balanced demand; alternate the next green phase for fairness."
            else:
                selected = "NS" if ns_score > ew_score else "EW"
                reason = (
                    f"{selected} selected by higher measured demand "
                    f"(NS score {ns_score:.1f}, EW score {ew_score:.1f})."
                )
            demand = max(ns_score, ew_score)
            duration = self.min_green_seconds + min(
                demand * 0.75, self.max_green_seconds - self.min_green_seconds
            )
            if demand == 0:
                duration = min(max(self.fixed_green_seconds, self.min_green_seconds), self.max_green_seconds)

        return SignalDecision(
            decided_at=now.isoformat(), selected_phase=selected,
            green_duration=round(min(max(duration, self.min_green_seconds), self.max_green_seconds), 1),
            observed_queues=queues, observed_metrics=observed, reason=reason,
        )

    def _bounded_fixed_duration(self) -> float:
        return round(min(max(self.fixed_green_seconds, self.min_green_seconds), self.max_green_seconds), 1)

    def _validate_snapshot(
        self, metrics: Mapping[str, Any] | None, now: datetime
    ) -> tuple[dict[str, int], dict[str, Any]]:
        if not isinstance(metrics, Mapping):
            raise ValueError("snapshot is missing")
        timestamp = metrics.get("simulation_timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ValueError("snapshot timestamp is missing or timezone-naive")
        age = (now - timestamp.astimezone(timezone.utc)).total_seconds()
        if age < -1 or age > self.telemetry_max_age_seconds:
            raise ValueError("snapshot is stale or from the future")

        raw_queues = metrics.get("queues")
        if not isinstance(raw_queues, Mapping):
            raise ValueError("directional queues are missing")
        queues: dict[str, int] = {}
        for direction in DIRECTIONS:
            value = raw_queues.get(direction)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {direction} queue")
            queues[direction] = int(value)

        def valid_nonnegative(name: str, default: float = 0.0) -> float:
            value = metrics.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
            return float(value)

        observed = {
            "total_vehicles": int(valid_nonnegative("total_vehicles")),
            "motorcycle_count": int(valid_nonnegative("motorcycle_count")),
            "car_count": int(valid_nonnegative("car_count")),
            "truck_count": int(valid_nonnegative("truck_count")),
            "average_waiting_time": valid_nonnegative("average_waiting_time"),
            "directional_counts": self._directional_mapping(metrics.get("directional_counts")),
            "directional_waiting_seconds": self._directional_mapping(
                metrics.get("directional_waiting_seconds"), floating=True
            ),
            "directional_vehicle_types": self._directional_types(metrics.get("directional_vehicle_types")),
            "age_seconds": round(max(0.0, age), 3),
        }
        return queues, observed

    @staticmethod
    def _directional_mapping(value: Any, floating: bool = False) -> dict[str, Any]:
        if value is None:
            return {direction: 0 for direction in DIRECTIONS}
        if not isinstance(value, Mapping):
            raise ValueError("invalid directional measurements")
        result: dict[str, Any] = {}
        for direction in DIRECTIONS:
            amount = value.get(direction, 0)
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
                raise ValueError("invalid directional measurements")
            result[direction] = float(amount) if floating else int(amount)
        return result

    @classmethod
    def _directional_types(cls, value: Any) -> dict[str, dict[str, int]]:
        if value is None:
            return {direction: {kind: 0 for kind in ("motorcycle", "car", "truck")} for direction in DIRECTIONS}
        if not isinstance(value, Mapping):
            raise ValueError("invalid directional vehicle categories")
        result = {}
        for direction in DIRECTIONS:
            categories = value.get(direction, {})
            if not isinstance(categories, Mapping):
                raise ValueError("invalid directional vehicle categories")
            result[direction] = {
                kind: int(cls._checked_count(categories.get(kind, 0)))
                for kind in ("motorcycle", "car", "truck")
            }
        return result

    @staticmethod
    def _checked_count(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid directional vehicle categories")
        return value

    @staticmethod
    def _axis_score(axis: str, queues: Mapping[str, int], observed: Mapping[str, Any]) -> float:
        directions = ("north", "south") if axis == "NS" else ("east", "west")
        queue_demand = sum(queues[direction] for direction in directions)
        categories = observed["directional_vehicle_types"]
        vehicle_demand = sum(
            categories[direction]["motorcycle"] * 0.2
            + categories[direction]["car"] * 0.25
            + categories[direction]["truck"] * 0.4
            for direction in directions
        )
        if vehicle_demand == 0:
            vehicle_demand = sum(observed["directional_counts"][direction] for direction in directions) * 0.25
        waiting_demand = sum(observed["directional_waiting_seconds"][direction] for direction in directions)
        return queue_demand * 3.0 + vehicle_demand + waiting_demand / 15.0
