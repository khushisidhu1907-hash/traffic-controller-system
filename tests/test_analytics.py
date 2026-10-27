from .test_metrics import payload


def test_analytics_summary_and_csv_export(client):
    client.post("/api/metrics", json=payload())
    client.post("/api/metrics", json=payload(total_vehicles=2, motorcycle_count=0,
                                               car_count=2, truck_count=0,
                                               maximum_queue_length=2))
    summary = client.get("/api/analytics/summary")
    assert summary.status_code == 200
    assert summary.json()["snapshot_count"] == 2
    assert summary.json()["peak_vehicles"] == 4
    assert summary.json()["average_vehicles"] == 3

    export = client.get("/api/analytics/export")
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
    assert "motorcycle_count" in export.text
    assert len(export.text.splitlines()) == 3


def test_empty_analytics_and_performance_have_no_invented_measurements(client):
    assert client.get("/api/analytics/summary").json()["snapshot_count"] == 0
    result = client.get("/api/analytics/performance").json()
    assert result["snapshot_count"] == 0
    assert result["available_measurements"] == []


def test_controller_decision_validation_and_persistence(client):
    response = client.post("/api/analytics/decisions", json={
        "selected_phase": "NS", "reason": "north queue longer",
        "observed_queues": {"north": 4, "south": 1, "east": 0, "west": 0},
        "controller_mode": "adaptive", "green_duration": 12
    })
    assert response.status_code == 201
    assert response.json()["controller_mode"] == "adaptive"
