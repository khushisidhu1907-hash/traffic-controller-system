from datetime import datetime, timedelta, timezone

from backend.services.controller_service import controller_runtime
from backend.services.signal_controller import (
    AdaptiveSignalController,
    clearance_transition,
    next_green_preview,
    permitted_green_axis,
)


def snapshot(**overrides):
    value = {
        "simulation_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_vehicles": 0,
        "motorcycle_count": 0,
        "car_count": 0,
        "truck_count": 0,
        "average_waiting_time": 0.0,
        "queues": {"north": 0, "south": 0, "east": 0, "west": 0},
        "directional_counts": {"north": 0, "south": 0, "east": 0, "west": 0},
        "directional_waiting_seconds": {"north": 0, "south": 0, "east": 0, "west": 0},
        "directional_vehicle_types": {
            direction: {"motorcycle": 0, "car": 0, "truck": 0}
            for direction in ("north", "south", "east", "west")
        },
    }
    value.update(overrides)
    return value


def test_empty_balanced_demand_alternates_and_uses_safe_duration():
    controller = AdaptiveSignalController(min_green_seconds=5, max_green_seconds=20)
    result = controller.decide(snapshot(), "NS")
    assert result.selected_phase == "EW"
    assert result.green_duration == 8


def test_heavy_north_south_queue_gets_longer_green():
    controller = AdaptiveSignalController(min_green_seconds=5, max_green_seconds=30)
    metrics = snapshot(queues={"north": 8, "south": 5, "east": 0, "west": 0})
    result = controller.decide(metrics, "EW")
    assert result.selected_phase == "NS"
    assert 5 <= result.green_duration <= 30
    assert result.green_duration > 8


def test_heavy_east_west_queue_is_selected():
    controller = AdaptiveSignalController()
    metrics = snapshot(queues={"north": 0, "south": 0, "east": 6, "west": 7})
    assert controller.decide(metrics, "NS").selected_phase == "EW"


def test_mixed_vehicle_types_and_waiting_time_inform_measured_decision():
    controller = AdaptiveSignalController()
    types = snapshot()["directional_vehicle_types"]
    types["west"]["truck"] = 3
    metrics = snapshot(
        total_vehicles=3,
        truck_count=3,
        directional_vehicle_types=types,
        directional_counts={"north": 0, "south": 0, "east": 0, "west": 3},
        directional_waiting_seconds={"north": 0, "south": 0, "east": 0, "west": 90},
    )
    result = controller.decide(metrics, "NS")
    assert result.selected_phase == "EW"
    assert result.observed_metrics["truck_count"] == 3
    assert result.observed_metrics["directional_waiting_seconds"]["west"] == 90


def test_minimum_and_maximum_green_are_enforced():
    controller = AdaptiveSignalController(min_green_seconds=4, max_green_seconds=12)
    empty = controller.decide(snapshot(), "NS")
    heavy = controller.decide(snapshot(queues={"north": 100, "south": 100, "east": 0, "west": 0}), "EW")
    assert empty.green_duration >= 4
    assert heavy.green_duration == 12


def test_maximum_red_time_prevents_starvation():
    controller = AdaptiveSignalController(max_red_seconds=20)
    metrics = snapshot(queues={"north": 0, "south": 0, "east": 20, "west": 20})
    result = controller.decide(metrics, "NS", {"NS": 21, "EW": 0})
    assert result.selected_phase == "NS"
    assert result.green_duration == controller.min_green_seconds
    assert "Fairness override" in result.reason


def test_stale_or_invalid_snapshot_falls_back_to_other_safe_axis():
    controller = AdaptiveSignalController(min_green_seconds=4, max_green_seconds=10)
    stale = snapshot(simulation_timestamp=(datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat())
    stale_result = controller.decide(stale, "NS")
    invalid_result = controller.decide({"queues": {}}, "EW")
    assert stale_result.selected_phase == "EW"
    assert "fallback" in stale_result.reason.lower()
    assert stale_result.green_duration == 8
    assert invalid_result.selected_phase == "NS"


def test_signal_cycle_requires_yellow_and_all_red_before_next_green_choice():
    assert clearance_transition("NS", 8, 8, 2, 1) == "NS_YELLOW"
    assert clearance_transition("NS_YELLOW", 2, 8, 2, 1) == "ALL_RED"
    assert clearance_transition("ALL_RED", 1, 8, 2, 1) == "GREEN_BOUNDARY"
    assert clearance_transition("EW", 7.9, 8, 2, 1) is None
    assert clearance_transition("EW_YELLOW", 1.9, 8, 2, 1) is None


def test_next_green_preview_uses_configured_phase_durations_and_mode():
    assert next_green_preview("NS", 3, 8, 2, 1, "NS", "fixed") == ("EW", 8)
    assert next_green_preview("EW_YELLOW", 0.5, 8, 2, 1, "EW", "fixed") == ("NS", 2.5)
    assert next_green_preview("ALL_RED", 0.25, 8, 2, 1, "NS", "fixed") == ("EW", 0.75)
    assert next_green_preview("NS", 3, 8, 2, 1, "NS", "adaptive") == ("ADAPTIVE_SELECTION", 8)


def test_phase_state_never_grants_both_conflicting_axes_green():
    for phase in ("NS", "NS_YELLOW", "EW", "EW_YELLOW", "ALL_RED"):
        axis = permitted_green_axis(phase)
        ns_green = axis == "NS"
        ew_green = axis == "EW"
        assert not (ns_green and ew_green)
    assert permitted_green_axis("ALL_RED") is None


def test_controller_mode_command_and_simulator_acknowledgement(client):
    controller_runtime.reset()
    requested = client.post("/api/controller/mode", json={"mode": "adaptive"})
    assert requested.status_code == 202
    assert requested.json()["requested_mode"] == "adaptive"
    assert requested.json()["active_mode"] is None
    assert requested.json()["pending"] is True
    assert client.get("/api/controller/command").json()["requested_mode"] == "adaptive"
    assert client.post("/api/controller/mode", json={"mode": "ml"}).status_code == 422

    decision = {
        "selected_phase": "EW",
        "reason": "Measured east-west queues were longer.",
        "observed_queues": {"north": 0, "south": 0, "east": 3, "west": 2},
        "observed_metrics": {"average_waiting_time": 2.5, "truck_count": 1},
        "controller_mode": "adaptive",
        "green_duration": 12.5,
    }
    report = client.post("/api/controller/state", json={
        "active_mode": "adaptive",
        "phase": "EW",
        "green_duration": 12.5,
        "command_revision": client.get("/api/controller/command").json()["revision"],
        "simulation_id": "test-simulation",
        "decision": decision,
    })
    assert report.status_code == 200
    assert report.json()["active_mode"] == "adaptive"
    assert report.json()["pending"] is False
    assert report.json()["simulation_connected"] is True
    assert client.get("/api/controller/decision").json()["selected_phase"] == "EW"
    fixed_requested = client.post("/api/controller/mode", json={"mode": "fixed"})
    assert fixed_requested.status_code == 202
    assert fixed_requested.json()["requested_mode"] == "fixed"
    assert fixed_requested.json()["active_mode"] == "adaptive"
    assert fixed_requested.json()["pending"] is True
    bad_ack = client.post("/api/controller/state", json={
        "active_mode": "adaptive",
        "phase": "NS",
        "green_duration": 8,
        "command_revision": fixed_requested.json()["command_revision"],
        "simulation_id": "test-simulation",
    })
    assert bad_ack.status_code == 409
    assert client.get("/api/controller/status").json()["active_mode"] == "adaptive"


def test_simulator_reconnects_after_api_restart_with_ahead_revision(client):
    controller_runtime.reset()
    report = client.post("/api/controller/state", json={
        "active_mode": "adaptive",
        "phase": "NS",
        "green_duration": 16,
        "command_revision": 4,
        "simulation_id": "still-running-simulator",
    })
    assert report.status_code == 200
    assert report.json()["requested_mode"] == "adaptive"
    assert report.json()["active_mode"] == "adaptive"
    assert report.json()["applied_revision"] == 4
    assert report.json()["pending"] is False
    assert client.get("/api/controller/performance").json()["measurement_source"].startswith("real simulator")


def test_new_simulator_process_resynchronizes_below_api_revision(client):
    controller_runtime.reset()
    first = client.post("/api/controller/state", json={
        "active_mode": "fixed", "phase": "NS", "green_duration": 8,
        "command_revision": 0, "simulation_id": "first-process",
    })
    assert first.status_code == 200
    requested = client.post("/api/controller/mode", json={"mode": "adaptive"})
    assert requested.status_code == 202
    assert requested.json()["command_revision"] == 1

    restarted = client.post("/api/controller/state", json={
        "active_mode": "fixed", "phase": "NS", "green_duration": 8,
        "command_revision": 0, "simulation_id": "restarted-process",
    })
    assert restarted.status_code == 200
    assert restarted.json()["pending"] is True
    assert client.get("/api/controller/command").json() == {"requested_mode": "adaptive", "revision": 1}

    applied = client.post("/api/controller/state", json={
        "active_mode": "adaptive", "phase": "EW", "green_duration": 12,
        "command_revision": 1, "simulation_id": "restarted-process",
    })
    assert applied.status_code == 200
    assert applied.json()["pending"] is False


def test_controller_status_exposes_simulator_timing_and_pause_state(client):
    report = client.post("/api/controller/state", json={
        "active_mode": "fixed",
        "phase": "EW_YELLOW",
        "green_duration": 8,
        "phase_duration_seconds": 2,
        "phase_elapsed_seconds": 0.5,
        "next_phase_preview": "NS",
        "next_phase_in_seconds": 2.5,
        "command_revision": 0,
        "simulation_id": "timed-simulator",
        "paused": True,
    })
    assert report.status_code == 200
    assert report.json()["phase_duration_seconds"] == 2
    assert report.json()["phase_elapsed_seconds"] == 0.5
    assert report.json()["next_phase_preview"] == "NS"
    assert report.json()["next_phase_in_seconds"] == 2.5
    assert report.json()["previous_phase"] is None
    assert report.json()["paused"] is True

    changed_phase = client.post("/api/controller/state", json={
        "active_mode": "fixed",
        "phase": "ALL_RED",
        "green_duration": 8,
        "phase_duration_seconds": 1,
        "phase_elapsed_seconds": 0,
        "next_phase_preview": "NS",
        "next_phase_in_seconds": 1,
        "command_revision": 0,
        "simulation_id": "timed-simulator",
        "paused": False,
    })
    assert changed_phase.status_code == 200
    assert changed_phase.json()["previous_phase"] == "EW_YELLOW"


def test_controller_timing_fields_must_be_valid_and_paired(client):
    report = client.post("/api/controller/state", json={
        "active_mode": "fixed",
        "phase": "NS",
        "green_duration": 8,
        "phase_duration_seconds": 8,
        "command_revision": 0,
        "simulation_id": "invalid-timing-simulator",
    })
    assert report.status_code == 422


def test_controller_performance_uses_mode_tagged_real_snapshots(client):
    from .test_metrics import payload

    for mode, waiting in (("fixed", 8.0), ("adaptive", 3.0)):
        value = payload(average_waiting_time=waiting)
        value["controller_mode"] = mode
        assert client.post("/api/metrics", json=value).status_code == 201
    result = client.get("/api/controller/performance").json()
    assert result["modes"]["fixed"]["snapshot_count"] == 1
    assert result["modes"]["adaptive"]["average_waiting_time_seconds"] == 3.0
    assert "not a per-mode throughput total" in result["note"]
