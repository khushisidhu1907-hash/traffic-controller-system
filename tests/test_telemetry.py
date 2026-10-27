import time
from types import SimpleNamespace

from backend.telemetry_client import TelemetryClient, build_simulation_metrics


def test_telemetry_counts_real_categories_and_approach_queues():
    vehicles = [
        SimpleNamespace(vehicle_type="motorcycle", direction="down", x=457, y=190, waiting_seconds=1.5, is_waiting=True),
        SimpleNamespace(vehicle_type="truck", direction="right", x=350, y=365, waiting_seconds=0.0, is_waiting=True),
        SimpleNamespace(vehicle_type="car", direction="up", x=515, y=460, waiting_seconds=3.0, is_waiting=True),
    ]
    result = build_simulation_metrics(vehicles, 9, "NS", 4, 500, 350, 180, 42)
    assert result["total_vehicles"] == 3
    assert result["motorcycle_count"] == 1
    assert result["truck_count"] == 1
    assert result["car_count"] == 1
    assert result["vehicles_passed"] == 9
    assert result["queues"] == {"north": 1, "south": 1, "east": 0, "west": 1}
    assert result["average_waiting_time"] == 1.5


def test_telemetry_client_handles_unavailable_backend_and_closes():
    client = TelemetryClient(base_url="http://127.0.0.1:1", interval_seconds=0)
    client.start()
    client.offer({"sample": True})
    started = time.monotonic()
    client.close(timeout=1)
    assert time.monotonic() - started < 2
    assert client._thread is not None
    assert not client._thread.is_alive()
