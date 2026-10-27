"""Nonblocking, latest-snapshot-wins telemetry sender for the Pygame process."""
import ipaddress
import json
import logging
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen

logger = logging.getLogger(__name__)


class TelemetryClient:
    """Send immutable snapshot dictionaries in a worker thread, never on the render loop."""

    def __init__(self, base_url: Optional[str] = None, interval_seconds: Optional[float] = None):
        self.base_url = (base_url or os.getenv("TRAFFIC_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.interval_seconds = (
            interval_seconds if interval_seconds is not None
            else float(os.getenv("TRAFFIC_TELEMETRY_INTERVAL", "1.0"))
        )
        self.request_timeout = float(os.getenv("TRAFFIC_API_TIMEOUT", "1.5"))
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._commands: queue.Queue = queue.Queue(maxsize=1)
        self._simulation_commands: queue.Queue = queue.Queue(maxsize=1)
        self._state_lock = threading.Lock()
        self._latest_controller_state: Optional[dict[str, Any]] = None
        self._pending_decisions: deque[dict[str, Any]] = deque()
        self._command_poll_interval = 0.5
        self._state_report_interval = 0.25
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_offer = 0.0
        self._last_error_log = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="traffic-telemetry", daemon=True)
        self._thread.start()

    def offer(self, snapshot: dict[str, Any]) -> None:
        """Queue a ready-to-send snapshot at the configured interval; never waits for I/O."""
        now = time.monotonic()
        if now - self._last_offer < self.interval_seconds:
            return
        self._last_offer = now
        try:
            self._queue.put_nowait(snapshot)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(snapshot)
            except queue.Full:
                pass

    def close(self, timeout: float = 1.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=max(timeout, self.request_timeout + 0.25))

    def drain_commands(self) -> list[dict[str, Any]]:
        """Return any polled commands for the simulation's main thread to apply."""
        commands = []
        while True:
            try:
                commands.append(self._commands.get_nowait())
            except queue.Empty:
                return commands

    def drain_simulation_commands(self) -> list[dict[str, Any]]:
        """Return simulation controls for application on the Pygame main thread."""
        commands = []
        while True:
            try:
                commands.append(self._simulation_commands.get_nowait())
            except queue.Empty:
                return commands

    def report_controller_state(self, state: dict[str, Any]) -> None:
        """Queue a main-thread state snapshot; all network I/O stays in the worker."""
        state_copy = dict(state)
        decision = state_copy.pop("decision", None)
        with self._state_lock:
            self._latest_controller_state = state_copy
            if decision is not None:
                self._pending_decisions.append(decision)

    def _run(self) -> None:
        last_command_poll = 0.0
        last_state_report = 0.0
        while not self._stop.is_set():
            try:
                snapshot = self._queue.get(timeout=0.1)
            except queue.Empty:
                snapshot = None
            if snapshot is not None:
                self._send_json("/api/metrics", snapshot)

            now = time.monotonic()
            if now - last_command_poll >= self._command_poll_interval:
                command = self._get_json("/api/controller/command")
                if command is not None:
                    try:
                        self._commands.put_nowait(command)
                    except queue.Full:
                        try:
                            self._commands.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._commands.put_nowait(command)
                        except queue.Full:
                            pass
                simulation_command = self._get_json("/api/simulation/command")
                if simulation_command is not None:
                    try:
                        self._simulation_commands.put_nowait(simulation_command)
                    except queue.Full:
                        try:
                            self._simulation_commands.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._simulation_commands.put_nowait(simulation_command)
                        except queue.Full:
                            pass
                last_command_poll = now

            if now - last_state_report >= self._state_report_interval:
                with self._state_lock:
                    state = dict(self._latest_controller_state) if self._latest_controller_state else None
                    decision = self._pending_decisions.popleft() if self._pending_decisions else None
                if state is not None:
                    if decision is not None:
                        state["decision"] = decision
                    if not self._send_json("/api/controller/state", state) and decision is not None:
                        with self._state_lock:
                            self._pending_decisions.appendleft(decision)
                last_state_report = now

    def _send_json(self, path: str, payload: dict[str, Any]) -> bool:
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._open_request(request) as response:
                return response.status < 300
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            self._log_unavailable(exc)
            return False

    def _get_json(self, path: str) -> Optional[dict[str, Any]]:
        request = Request(self.base_url + path, headers={"Accept": "application/json"})
        try:
            with self._open_request(request) as response:
                value = json.loads(response.read().decode("utf-8"))
                return value if isinstance(value, dict) else None
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            self._log_unavailable(exc)
            return None

    def _open_request(self, request: Request):
        host = urlsplit(request.full_url).hostname
        try:
            is_loopback = host == "localhost" or (
                host is not None and ipaddress.ip_address(host).is_loopback
            )
        except ValueError:
            is_loopback = False
        if is_loopback:
            return build_opener(ProxyHandler({})).open(
                request, timeout=min(self.request_timeout, 0.5)
            )
        return urlopen(request, timeout=self.request_timeout)

    def _log_unavailable(self, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_error_log >= 30:
            logger.warning("Traffic API unavailable (%s); simulation continues", exc)
            self._last_error_log = now


def build_simulation_metrics(vehicles, passed: int, phase: str, density: int,
                             cx: int, cy: int, road_width: int, car_height: int,
                             controller_mode: str = "fixed") -> dict[str, Any]:
    """Derive API telemetry from a main-thread snapshot of the actual Pygame vehicles."""
    counts = {"motorcycle": 0, "car": 0, "truck": 0}
    queues = {"north": 0, "south": 0, "east": 0, "west": 0}
    directional_counts = {direction: 0 for direction in queues}
    directional_waiting_seconds = {direction: 0.0 for direction in queues}
    direction_for_vehicle = {"down": "north", "up": "south", "right": "west", "left": "east"}
    waiting = []
    approach_depth = 150
    stop_north = cy - road_width // 2 - 8
    stop_south = cy + road_width // 2 + 8
    stop_west = cx - road_width // 2 - 8
    stop_east = cx + road_width // 2 + 8

    for vehicle in vehicles:
        kind = getattr(vehicle, "vehicle_type", "car")
        counts[kind if kind in counts else "car"] += 1
        waiting.append(float(getattr(vehicle, "waiting_seconds", 0.0)))
        direction = vehicle.direction
        approach = direction_for_vehicle.get(direction)
        if approach is not None:
            directional_counts[approach] += 1
            directional_waiting_seconds[approach] += float(getattr(vehicle, "waiting_seconds", 0.0))
        queued = bool(getattr(vehicle, "is_waiting", False))
        if queued and direction == "down" and stop_north - approach_depth <= vehicle.y + car_height <= stop_north + 2:
            queues["north"] += 1
        elif queued and direction == "up" and stop_south - 2 <= vehicle.y <= stop_south + approach_depth:
            queues["south"] += 1
        elif queued and direction == "right" and stop_west - approach_depth <= vehicle.x + car_height <= stop_west + 2:
            queues["west"] += 1
        elif queued and direction == "left" and stop_east - 2 <= vehicle.x <= stop_east + approach_depth:
            queues["east"] += 1

    return {
        "simulation_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_vehicles": len(vehicles),
        "motorcycle_count": counts["motorcycle"],
        "car_count": counts["car"],
        "truck_count": counts["truck"],
        "vehicles_passed": int(passed),
        "queues": queues,
        "average_waiting_time": sum(waiting) / len(waiting) if waiting else 0.0,
        "maximum_queue_length": max(queues.values()),
        "signal_phase": phase,
        "traffic_density": int(density),
        "controller_mode": controller_mode,
        "directional_counts": directional_counts,
        "directional_waiting_seconds": directional_waiting_seconds,
        "directional_vehicle_types": {
            direction: {
                kind: sum(
                    1 for vehicle in vehicles
                    if direction_for_vehicle.get(getattr(vehicle, "direction", "")) == direction
                    and getattr(vehicle, "vehicle_type", "car") == kind
                )
                for kind in counts
            }
            for direction in queues
        },
    }
