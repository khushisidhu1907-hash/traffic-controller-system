from datetime import datetime, timedelta, timezone

from .test_metrics import payload


def test_assistant_responds_without_fabricating_missing_telemetry(client):
    response = client.post("/api/traffic-assistant", json={
        "message": "Analyze Traffic",
        "action": "analyze_traffic",
    })
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "rule_based_demo"
    assert "don't have a recorded traffic snapshot" in result["response"]
    assert "forecast" not in result["response"].lower()


def test_assistant_uses_recorded_queues_and_gives_bounded_advisory(client):
    telemetry = payload(
        simulation_timestamp=datetime.now(timezone.utc).isoformat(),
        queues={"north": 2, "south": 1, "east": 7, "west": 2},
        maximum_queue_length=7,
    )
    assert client.post("/api/metrics", json=telemetry).status_code == 201

    congestion = client.post("/api/traffic-assistant", json={
        "message": "Check Congestion",
        "action": "check_congestion",
    }).json()["response"]
    assert "Intersection 01" in congestion
    assert "east approach" in congestion
    assert "7" in congestion

    optimization = client.post("/api/traffic-assistant", json={
        "message": "Optimize Signals",
        "action": "optimize_signals",
    }).json()["response"]
    assert "EW axis" in optimization
    assert "not a signal command" in optimization
    assert "configured" in optimization


def test_assistant_describes_history_without_claiming_model_forecast(client):
    for index, queue in enumerate((1, 2, 5, 7)):
        telemetry = payload(
            simulation_timestamp=(datetime.now(timezone.utc) - timedelta(seconds=4 - index)).isoformat(),
            queues={"north": queue, "south": 0, "east": 0, "west": 0},
            maximum_queue_length=queue,
        )
        assert client.post("/api/metrics", json=telemetry).status_code == 201

    result = client.post("/api/traffic-assistant", json={
        "message": "Predict Traffic",
        "action": "predict_traffic",
    }).json()["response"]
    assert "Historical trend only" in result
    assert "not a model forecast" in result
    assert "increasing" in result


def test_emergency_action_is_simulated_and_does_not_issue_controls(client):
    before = client.get("/api/controller/status").json()
    result = client.post("/api/traffic-assistant", json={
        "message": "Emergency Mode",
        "action": "emergency_mode",
    })
    assert result.status_code == 200
    assert "SIMULATED EMERGENCY PROCEDURE" in result.json()["response"]
    assert "no signal was changed" in result.json()["response"]
    assert client.get("/api/controller/status").json()["command_revision"] == before["command_revision"]


def test_assistant_validates_message_and_action(client):
    assert client.post("/api/traffic-assistant", json={"message": "  "}).status_code == 422
    assert client.post("/api/traffic-assistant", json={"message": "x" * 1001}).status_code == 422
    assert client.post("/api/traffic-assistant", json={
        "message": "help",
        "action": "launch_real_signal",
    }).status_code == 422
