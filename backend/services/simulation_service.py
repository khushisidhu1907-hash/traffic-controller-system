"""Thread-safe simulation command and acknowledgement state."""
from datetime import datetime, timezone
from threading import RLock
from typing import Optional


class SimulationControl:
    def __init__(self) -> None:
        self._lock = RLock()
        self.simulation_id: Optional[str] = None
        self.requested_paused = False
        self.paused = False
        self.requested_density = 3
        self.density = 3
        self.command_revision = 0
        self.applied_revision = 0
        self.reset_revision = 0
        self.applied_reset_revision = 0
        self.last_requested_action = "idle"
        self.requested_by = "system"
        self.requested_at: Optional[datetime] = None

    def reset(self) -> None:
        """Clear in-memory simulator state for isolated tests and API restarts."""
        with self._lock:
            self.simulation_id = None
            self.requested_paused = False
            self.paused = False
            self.requested_density = 3
            self.density = 3
            self.command_revision = 0
            self.applied_revision = 0
            self.reset_revision = 0
            self.applied_reset_revision = 0
            self.last_requested_action = "idle"
            self.requested_by = "system"
            self.requested_at = None

    def request(self, action: str, requested_by: str = "api") -> dict:
        with self._lock:
            if action in ("start", "resume"):
                self.requested_paused = False
            elif action == "pause":
                self.requested_paused = True
            elif action == "reset":
                self.reset_revision += 1
            else:
                raise ValueError(f"unsupported simulation action: {action}")
            self.command_revision += 1
            self.last_requested_action = action
            self.requested_by = requested_by
            self.requested_at = datetime.now(timezone.utc)
            return self.command()

    def set_density(self, density: int) -> dict:
        with self._lock:
            self.requested_density = density
            self.command_revision += 1
            self.last_requested_action = "density"
            self.requested_at = datetime.now(timezone.utc)
            return self.command()

    def command(self) -> dict:
        with self._lock:
            return {
                "revision": self.command_revision,
                "paused": self.requested_paused,
                "density": self.requested_density,
                "reset_revision": self.reset_revision,
            }

    def acknowledge(self, report: dict) -> dict:
        with self._lock:
            revision = report["simulation_control_revision"]
            if self.simulation_id != report["simulation_id"]:
                # Reconnect or API restart: register the simulator's current state.
                self.simulation_id = report["simulation_id"]
                self.paused = report["paused"]
                self.density = report["density"]
                self.applied_revision = revision
                self.applied_reset_revision = report["reset_revision"]
                if revision > self.command_revision:
                    self.command_revision = revision
                    self.requested_paused = self.paused
                    self.requested_density = self.density
                self.reset_revision = max(self.reset_revision, self.applied_reset_revision)
                return self.status()

            if revision > self.command_revision:
                raise ValueError("simulator acknowledgement is for an unknown command revision")
            if revision < self.applied_revision:
                raise ValueError("simulator acknowledgement is older than the applied state")
            self.paused = report["paused"]
            self.density = report["density"]
            self.applied_revision = revision
            self.applied_reset_revision = max(self.applied_reset_revision, report["reset_revision"])
            if revision == self.command_revision:
                # Also reflect changes made from the Pygame keyboard.
                self.requested_paused = self.paused
                self.requested_density = self.density
            return self.status()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.simulation_id is not None,
                "paused": self.paused,
                "density": self.density,
                "requested_paused": self.requested_paused,
                "requested_density": self.requested_density,
                "pending": self.applied_revision < self.command_revision,
                "command_revision": self.command_revision,
                "applied_revision": self.applied_revision,
                "reset_revision": self.reset_revision,
                "applied_reset_revision": self.applied_reset_revision,
                "last_requested_action": self.last_requested_action,
                "requested_by": self.requested_by,
                "requested_at": self.requested_at,
                "simulation_id": self.simulation_id,
            }

    def status(self, connected: bool = True) -> dict:
        state = self.snapshot()
        state["running"] = connected
        state["telemetry_connected"] = connected
        state["control_mode"] = "simulator_acknowledged"
        state["control_state_is_api_side_only"] = False
        return state


simulation_control = SimulationControl()
