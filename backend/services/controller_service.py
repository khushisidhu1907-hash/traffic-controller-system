"""Thread-safe command and acknowledgement state shared by API requests."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any


class ControllerRuntime:
    def __init__(self) -> None:
        self._lock = RLock()
        self.requested_mode = "fixed"
        self.active_mode: str | None = None
        self.command_revision = 0
        self.applied_revision = 0
        self.simulation_id: str | None = None
        self.phase: str | None = None
        self.green_duration: float | None = None
        self.phase_duration_seconds: float | None = None
        self.phase_elapsed_seconds: float | None = None
        self.next_phase_preview: str | None = None
        self.next_phase_in_seconds: float | None = None
        self.previous_phase: str | None = None
        self.paused = False
        self.updated_at: datetime | None = None
        self.last_decision: dict[str, Any] | None = None

    def reset(self) -> None:
        """Clear in-memory simulator state; intended for isolated tests/restarts."""
        with self._lock:
            self.requested_mode = "fixed"
            self.active_mode = None
            self.command_revision = 0
            self.applied_revision = 0
            self.simulation_id = None
            self.phase = None
            self.green_duration = None
            self.phase_duration_seconds = None
            self.phase_elapsed_seconds = None
            self.next_phase_preview = None
            self.next_phase_in_seconds = None
            self.previous_phase = None
            self.paused = False
            self.updated_at = None
            self.last_decision = None

    def request_mode(self, mode: str) -> dict[str, Any]:
        with self._lock:
            self.requested_mode = mode
            self.command_revision += 1
            return self.command()

    def command(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requested_mode": self.requested_mode,
                "revision": self.command_revision,
            }

    def acknowledge(self, report: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            revision = report["command_revision"]
            if self.simulation_id is None or report["simulation_id"] != self.simulation_id:
                # Either process may restart independently. A new simulator
                # identity starts with a local revision that can be lower than
                # the API's previous applied revision; register it, then let
                # the normal command channel apply any outstanding request.
                if revision > self.command_revision:
                    self.command_revision = revision
                    self.requested_mode = report["active_mode"]
                self.active_mode = report["active_mode"]
                self.phase = report["phase"]
                self.green_duration = report["green_duration"]
                self.phase_duration_seconds = report.get("phase_duration_seconds")
                self.phase_elapsed_seconds = report.get("phase_elapsed_seconds")
                self.next_phase_preview = report.get("next_phase_preview")
                self.next_phase_in_seconds = report.get("next_phase_in_seconds")
                self.previous_phase = None
                self.paused = report.get("paused", False)
                self.simulation_id = report["simulation_id"]
                self.updated_at = datetime.now(timezone.utc)
                self.applied_revision = revision
                if report.get("decision") is not None:
                    self.last_decision = report["decision"]
                return self.status()
            if revision > self.command_revision:
                raise ValueError("simulator acknowledgement is for an unknown command revision")
            if revision < self.applied_revision:
                raise ValueError("simulator acknowledgement is older than the applied state")
            if revision == self.command_revision and report["active_mode"] != self.requested_mode:
                raise ValueError("active mode does not match the requested mode for this revision")
            if report["phase"] != self.phase:
                self.previous_phase = self.phase
            self.active_mode = report["active_mode"]
            self.phase = report["phase"]
            self.green_duration = report["green_duration"]
            self.phase_duration_seconds = report.get("phase_duration_seconds")
            self.phase_elapsed_seconds = report.get("phase_elapsed_seconds")
            self.next_phase_preview = report.get("next_phase_preview")
            self.next_phase_in_seconds = report.get("next_phase_in_seconds")
            self.paused = report.get("paused", False)
            self.simulation_id = report["simulation_id"]
            self.updated_at = datetime.now(timezone.utc)
            self.applied_revision = revision
            if report.get("decision") is not None:
                self.last_decision = report["decision"]
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requested_mode": self.requested_mode,
                "active_mode": self.active_mode,
                "pending": self.active_mode != self.requested_mode or self.applied_revision < self.command_revision,
                "command_revision": self.command_revision,
                "applied_revision": self.applied_revision,
                "simulation_id": self.simulation_id,
                "phase": self.phase,
                "green_duration": self.green_duration,
                "phase_duration_seconds": self.phase_duration_seconds,
                "phase_elapsed_seconds": self.phase_elapsed_seconds,
                "next_phase_preview": self.next_phase_preview,
                "next_phase_in_seconds": self.next_phase_in_seconds,
                "previous_phase": self.previous_phase,
                "paused": self.paused,
                "updated_at": self.updated_at,
                "last_decision": self.last_decision,
            }


controller_runtime = ControllerRuntime()