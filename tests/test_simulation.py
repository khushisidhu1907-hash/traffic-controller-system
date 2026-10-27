from .test_metrics import payload


def test_simulation_control_requests_are_rejected_without_live_simulator(client):
    status = client.get("/api/simulation/status").json()
    assert status["control_state_is_api_side_only"] is False
    assert status["telemetry_connected"] is False

    assert client.post("/api/simulation/start", json={}).status_code == 409
    assert client.post("/api/simulation/pause", json={}).status_code == 409
    assert client.post("/api/simulation/reset", json={}).status_code == 409
    assert client.post("/api/simulation/density", json={"density": 8}).status_code == 409
    assert client.post("/api/simulation/density", json={"density": 11}).status_code == 422


def test_simulation_controls_reach_simulator_and_wait_for_acknowledgement(client):
    registered = client.post("/api/controller/state", json={
        "active_mode": "fixed", "phase": "NS", "green_duration": 8,
        "command_revision": 0, "simulation_id": "scenario-simulator",
        "simulation_control_revision": 0, "paused": False, "density": 3,
        "reset_revision": 0,
    })
    assert registered.status_code == 200
    assert client.get("/api/simulation/status").json()["telemetry_connected"] is True

    pause = client.post("/api/simulation/pause", json={"requested_by": "test"})
    assert pause.status_code == 202
    assert pause.json()["pending"] is True
    assert pause.json()["requested_paused"] is True
    command = client.get("/api/simulation/command").json()
    assert command == {"revision": 1, "paused": True, "density": 3, "reset_revision": 0}

    acknowledged = client.post("/api/controller/state", json={
        "active_mode": "fixed", "phase": "NS", "green_duration": 8,
        "command_revision": 0, "simulation_id": "scenario-simulator",
        "simulation_control_revision": 1, "paused": True, "density": 3,
        "reset_revision": 0,
    })
    assert acknowledged.status_code == 200
    status = client.get("/api/simulation/status").json()
    assert status["paused"] is True
    assert status["pending"] is False

    density = client.post("/api/simulation/density", json={"density": 8})
    assert density.status_code == 202
    assert client.get("/api/simulation/command").json()["density"] == 8
    reset = client.post("/api/simulation/reset", json={})
    assert reset.status_code == 202
    assert client.get("/api/simulation/command").json()["reset_revision"] == 1
    assert client.get("/api/simulation/command").json()["paused"] is True

    applied = client.post("/api/controller/state", json={
        "active_mode": "fixed", "phase": "NS", "green_duration": 8,
        "command_revision": 0, "simulation_id": "scenario-simulator",
        "simulation_control_revision": 3, "paused": True, "density": 8,
        "reset_revision": 1,
    })
    assert applied.status_code == 200
    status = client.get("/api/simulation/status").json()
    assert status["pending"] is False
    assert status["density"] == 8
    assert status["applied_reset_revision"] == 1


def test_valid_telemetry_marks_connection_fresh(client):
    response = client.post("/api/metrics", json=payload())
    assert response.status_code == 201
    assert client.get("/api/status").json()["simulation_connected"] is True
