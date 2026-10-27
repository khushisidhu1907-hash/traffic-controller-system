import backend.database as database
from backend.models import MetricSnapshot


def payload(**updates):
    data = {
        "simulation_timestamp": "2026-10-18T12:00:00Z",
        "total_vehicles": 4,
        "motorcycle_count": 1,
        "car_count": 2,
        "truck_count": 1,
        "vehicles_passed": 14,
        "queues": {"north": 1, "south": 0, "east": 2, "west": 0},
        "average_waiting_time": 3.5,
        "maximum_queue_length": 2,
        "signal_phase": "NS",
        "traffic_density": 3,
    }
    data.update(updates)
    return data


def test_metrics_submission_retrieval_and_database_persistence(client):
    submitted = client.post("/api/metrics", json=payload())
    assert submitted.status_code == 201
    snapshot_id = submitted.json()["id"]
    assert client.get("/api/metrics").json()["id"] == snapshot_id
    db = database.SessionLocal()
    try:
        assert db.get(MetricSnapshot, snapshot_id) is not None
    finally:
        db.close()


def test_negative_counts_and_wait_are_rejected(client):
    assert client.post("/api/metrics", json=payload(total_vehicles=-1)).status_code == 422
    assert client.post("/api/metrics", json=payload(average_waiting_time=-0.1)).status_code == 422
    assert client.post("/api/metrics", json=payload(queues={"north": -1, "south": 0, "east": 0, "west": 0})).status_code == 422
    timestamp_response = client.post("/api/metrics", json=payload(simulation_timestamp="2026-10-18T12:00:00"))
    assert timestamp_response.status_code == 201
    assert timestamp_response.json()["simulation_timestamp"].endswith("Z")


def test_latest_unavailable_and_empty_history(client):
    assert client.get("/api/metrics").status_code == 503
    history = client.get("/api/metrics/history")
    assert history.status_code == 200
    assert history.json() == []


def test_history_pagination(client):
    for seconds in range(3):
        data = payload(simulation_timestamp=f"2026-10-18T12:00:0{seconds}Z")
        assert client.post("/api/metrics", json=data).status_code == 201
    page = client.get("/api/metrics/history?limit=2&offset=1")
    assert page.status_code == 200
    assert len(page.json()) == 2
