"""Rule-based dashboard assistance grounded in stored simulator telemetry."""
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.models import MetricSnapshot
from backend.services.controller_service import controller_runtime

DIRECTIONS = ("north", "south", "east", "west")
ACTION_NAMES = {
    "analyze_traffic": "analyze",
    "check_congestion": "congestion",
    "optimize_signals": "optimize",
    "predict_traffic": "history",
    "emergency_mode": "emergency",
}


def _intent(message: str, action: str | None) -> str:
    if action:
        return ACTION_NAMES[action]
    text = message.casefold()
    if any(term in text for term in ("emergency", "ambulance", "fire truck", "priority")):
        return "emergency"
    if any(term in text for term in ("predict", "forecast", "trend", "history")):
        return "history"
    if any(term in text for term in ("optimize", "optimise", "green", "signal", "timing")):
        return "optimize"
    if any(term in text for term in ("congestion", "congested", "queue", "jam", "bottleneck")):
        return "congestion"
    return "analyze"


def _freshness(snapshot: MetricSnapshot | None, now: datetime) -> tuple[bool, str]:
    if snapshot is None:
        return False, "No traffic telemetry has been recorded yet."
    received = snapshot.received_at
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    age = max(0.0, (now - received.astimezone(timezone.utc)).total_seconds())
    if age <= 15:
        return True, f"Latest dashboard telemetry, received {age:.0f}s ago."
    return False, f"Last recorded dashboard telemetry, received {age / 60:.0f}m ago (stale)."


def _best_approach(snapshot: MetricSnapshot) -> tuple[str, int]:
    direction, count = max(snapshot.queues.items(), key=lambda item: item[1])
    return direction, count


def _response(
    intent: str,
    snapshot: MetricSnapshot | None,
    history: list[MetricSnapshot],
    controller: dict[str, Any],
    now: datetime,
) -> str:
    connected = bool(
        controller["updated_at"]
        and (now - controller["updated_at"].astimezone(timezone.utc)).total_seconds() <= 5
    )
    phase = controller["phase"] if connected else None
    if snapshot is None:
        if intent == "emergency":
            return (
                "DEMO / SIMULATED GUIDANCE — This assistant does not control real signals. "
                "For a real emergency, follow local emergency procedures and contact the appropriate "
                "dispatch service. In this simulation, identify the responding approach, keep conflicting "
                "movements stopped, and verify the route is clear before releasing traffic. No signal "
                "priority command was sent."
            )
        return (
            "I don't have a recorded traffic snapshot yet, so I can't assess current density, queues, "
            "or signal conditions. Start the simulator and wait for telemetry. You can still use the "
            "dashboard's live connection status to check whether it is reporting."
        )

    queues = {direction: int(snapshot.queues.get(direction, 0)) for direction in DIRECTIONS}
    direction, count = _best_approach(snapshot)
    axis_totals = {
        "NS": queues["north"] + queues["south"],
        "EW": queues["east"] + queues["west"],
    }
    status_text = f"Last simulator-acknowledged phase: {phase}." if phase else (
        f"Last recorded signal phase: {snapshot.signal_phase}; simulator heartbeat is not current."
    )

    if intent == "emergency":
        return (
            "DEMO / SIMULATED EMERGENCY PROCEDURE — Emergency vehicle detection is not available here, "
            "and no signal was changed. In this simulation, identify the responding vehicle's approach, keep conflicting "
            "traffic stopped, verify the intersection is clear, and release the route only after it passes. "
            "For real-world incidents, follow authorized emergency and traffic-control procedures; do not "
            "use this dashboard as a real signal-priority system."
        )

    if intent == "history":
        points = list(reversed(history))
        if len(points) < 2:
            return (
                f"{len(points)} stored telemetry sample(s) are available, which is not enough to describe "
                "a trend. There is no configured or implemented forecasting model, so I can't produce a "
                "future-traffic prediction. Collect more simulator history and ask again."
            )
        midpoint = len(points) // 2
        totals = [sum(int(point.queues.get(name, 0)) for name in DIRECTIONS) for point in points]
        earlier = sum(totals[:midpoint]) / midpoint
        later = sum(totals[midpoint:]) / (len(totals) - midpoint)
        delta = later - earlier
        trend = "increasing" if delta >= 1 else "decreasing" if delta <= -1 else "roughly steady"
        return (
            f"Historical trend only — not a model forecast. Across {len(points)} stored telemetry "
            f"snapshots, the average combined approach queue moved from {earlier:.1f} in the earlier "
            f"samples to {later:.1f} in the later samples ({trend}). Latest total: "
            f"{sum(queues.values())} queued vehicles. No future volume estimate is available."
        )

    if intent == "optimize":
        axis = max(axis_totals, key=axis_totals.get)
        if axis_totals["NS"] == axis_totals["EW"]:
            axis = "NS" if phase != "NS" else "EW"
        min_green = float(os.getenv("TRAFFIC_MIN_GREEN_SECONDS", "5"))
        max_green = float(os.getenv("TRAFFIC_MAX_GREEN_SECONDS", "30"))
        default_green = 8.0
        demand = axis_totals[axis]
        suggested = min(max_green, max(min_green, min_green + 1.5 * demand)) if demand else min(
            max_green, max(min_green, default_green)
        )
        return (
            f"Advisory estimate, not a signal command: measured queues favor the {axis} axis "
            f"({axis_totals[axis]} vehicles vs {axis_totals['EW' if axis == 'NS' else 'NS']}). "
            f"A simple queue-based starting point is about {suggested:.0f}s of green, bounded by the "
            f"configured {min_green:.0f}–{max_green:.0f}s limits. Confirm local safety and fairness "
            f"requirements before applying any timing. {status_text}"
        )

    if intent == "congestion":
        level = "high" if count >= 8 else "building" if count >= 4 else "low"
        return (
            f"This dashboard reports Intersection 01. Its {direction} approach has the largest measured "
            f"queue ({count}); current maximum queue is {snapshot.maximum_queue_length}. Congestion level "
            f"is {level} by the assistant's simple "
            "queue bands (low <4, building 4–7, high ≥8 vehicles). "
            f"Other approach queues: N {queues['north']}, S {queues['south']}, "
            f"E {queues['east']}, W {queues['west']}. {status_text}"
        )

    fresh, age_note = _freshness(snapshot, now)
    freshness_warning = "" if fresh else " Treat these figures as historical, not live. "
    return (
        f"{age_note} The snapshot reports {snapshot.total_vehicles} vehicles on the road, "
        f"traffic density {snapshot.traffic_density}/10, average waiting {snapshot.average_waiting_time:.1f}s, "
        f"and queues N {queues['north']}, S {queues['south']}, E {queues['east']}, W {queues['west']}. "
        f"Intersection 01's busiest approach is {direction} ({count} queued). "
        f"{status_text}{freshness_warning}"
        "These are simulator telemetry estimates, not independent roadside sensor measurements."
    )


def answer_traffic_question(db: Session, message: str, action: str | None = None) -> dict[str, Any]:
    """Answer from persisted telemetry and simulator-acknowledged in-memory state."""
    now = datetime.now(timezone.utc)
    snapshot = db.scalar(
        select(MetricSnapshot)
        .order_by(desc(MetricSnapshot.received_at), desc(MetricSnapshot.id))
        .limit(1)
    )
    history = list(
        db.scalars(
            select(MetricSnapshot)
            .order_by(desc(MetricSnapshot.received_at), desc(MetricSnapshot.id))
            .limit(10)
        )
    )
    controller = controller_runtime.status()
    return {
        "response": _response(_intent(message, action), snapshot, history, controller, now),
        "mode": "rule_based_demo",
        "data_source": "stored simulator telemetry and simulator-acknowledged controller state",
        "generated_at": now,
    }
