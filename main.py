
import random
import uuid
import pygame
from backend.telemetry_client import TelemetryClient, build_simulation_metrics
from backend.services.signal_controller import (
    AdaptiveSignalController,
    clearance_transition,
    next_green_preview,
    permitted_green_axis,
)

# -------------------- Configuration --------------------
pygame.init()

WIDTH, HEIGHT = 1000, 700
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Autonomous Traffic System - Simulation")

clock = pygame.time.Clock()
font = pygame.font.SysFont("Arial", 20)
small_font = pygame.font.SysFont("Arial", 15)

BG = (45, 105, 65)
ROAD = (55, 58, 64)
WHITE = (245, 245, 245)
YELLOW = (245, 195, 55)
RED = (235, 65, 65)
GREEN = (55, 220, 105)
DARK = (25, 28, 34)

CX, CY = WIDTH // 2, HEIGHT // 2
ROAD_W = 180

# Signal timings, in seconds
GREEN_TIME = 8
YELLOW_TIME = 2
ALL_RED_TIME = 1
PHASE_DURATION = GREEN_TIME + YELLOW_TIME

# Vehicle dimensions and speed
CAR_W, CAR_H = 28, 42
CAR_SPEED = 2.5
SPAWN_INTERVAL = 0.9

# Each direction has its own lane.
# down: travels from top to bottom
# up: travels from bottom to top
# right: travels from left to right
# left: travels from right to left
DIRECTIONS = ["down", "up", "right", "left"]
CAR_COLORS = [
    (80, 170, 255),
    (255, 155, 65),
    (205, 120, 245),
    (245, 235, 210),
    (80, 220, 200),
]


class Vehicle:
    def __init__(self, direction):
        self.direction = direction
        self.vehicle_type = random.choices(
            ["car", "motorcycle", "truck"], weights=[0.65, 0.25, 0.10]
        )[0]
        self.color = random.choice(CAR_COLORS)
        self.speed = CAR_SPEED * random.uniform(0.85, 1.12)
        self.waiting_seconds = 0.0
        self.is_waiting = False

        if direction == "down":
            self.x, self.y = CX - 43, -CAR_H
        elif direction == "up":
            self.x, self.y = CX + 15, HEIGHT + CAR_H
        elif direction == "right":
            self.x, self.y = -CAR_H, CY + 15
        else:
            self.x, self.y = WIDTH + CAR_H, CY - 43

    def rect(self):
        if self.direction in ("down", "up"):
            return pygame.Rect(
                int(self.x), int(self.y), CAR_W, CAR_H
            )
        return pygame.Rect(
            int(self.x), int(self.y), CAR_H, CAR_W
        )

    def update(self, vehicles, allowed, dt=1.0 / 60.0):
        # Do not let a vehicle cross the stop line on red.
        # Vehicles already past the line can clear the intersection.
        can_move = True

        if not allowed:
            if self.direction == "down":
                front = self.y + CAR_H
                stop_line = CY - ROAD_W // 2 - 8
                if front <= stop_line and front + self.speed > stop_line:
                    can_move = False
            elif self.direction == "up":
                front = self.y
                stop_line = CY + ROAD_W // 2 + 8
                if front >= stop_line and front - self.speed < stop_line:
                    can_move = False
            elif self.direction == "right":
                front = self.x + CAR_H
                stop_line = CX - ROAD_W // 2 - 8
                if front <= stop_line and front + self.speed > stop_line:
                    can_move = False
            else:
                front = self.x
                stop_line = CX + ROAD_W // 2 + 8
                if front >= stop_line and front - self.speed < stop_line:
                    can_move = False

        # Keep a safe gap from the next vehicle in this lane.
        gap = 8
        for other in vehicles:
            if other is self or other.direction != self.direction:
                continue

            if self.direction == "down":
                same_lane = abs(self.x - other.x) < 2
                ahead = other.y > self.y
                distance = other.y - (self.y + CAR_H)

            elif self.direction == "up":
                same_lane = abs(self.x - other.x) < 2
                ahead = other.y < self.y
                distance = self.y - (other.y + CAR_H)

            elif self.direction == "right":
                same_lane = abs(self.y - other.y) < 2
                ahead = other.x > self.x
                distance = other.x - (self.x + CAR_H)

            else:
                same_lane = abs(self.y - other.y) < 2
                ahead = other.x < self.x
                distance = self.x - (other.x + CAR_H)

            if same_lane and ahead and 0 <= distance < 34:
                can_move = False
                break

        if can_move:
            self.is_waiting = False
            if self.direction == "down":
                self.y += self.speed
            elif self.direction == "up":
                self.y -= self.speed
            elif self.direction == "right":
                self.x += self.speed
            else:
                self.x -= self.speed
        else:
            self.is_waiting = True
            self.waiting_seconds += dt

    def draw(self, surface):
        rect = self.rect()
        pygame.draw.rect(surface, self.color, rect, border_radius=6)

        # Small windshield
        if self.direction in ("down", "up"):
            glass = pygame.Rect(rect.x + 5, rect.y + 8, CAR_W - 10, 10)
        else:
            glass = pygame.Rect(rect.x + 8, rect.y + 5, 10, CAR_W - 10)

        pygame.draw.rect(surface, (35, 45, 55), glass, border_radius=3)


def draw_road():
    screen.fill(BG)

    # Crossroads
    pygame.draw.rect(
        screen, ROAD,
        (CX - ROAD_W // 2, 0, ROAD_W, HEIGHT)
    )
    pygame.draw.rect(
        screen, ROAD,
        (0, CY - ROAD_W // 2, WIDTH, ROAD_W)
    )

    # Lane markings
    for y in range(0, HEIGHT, 34):
        pygame.draw.line(screen, YELLOW, (CX, y), (CX, y + 16), 2)

    for x in range(0, WIDTH, 34):
        pygame.draw.line(screen, YELLOW, (x, CY), (x + 16, CY), 2)

    # Stop lines
    pygame.draw.line(
        screen, WHITE,
        (CX - ROAD_W // 2, CY - ROAD_W // 2 - 8),
        (CX - 5, CY - ROAD_W // 2 - 8), 4
    )
    pygame.draw.line(
        screen, WHITE,
        (CX + 5, CY + ROAD_W // 2 + 8),
        (CX + ROAD_W // 2, CY + ROAD_W // 2 + 8), 4
    )
    pygame.draw.line(
        screen, WHITE,
        (CX - ROAD_W // 2 - 8, CY + 5),
        (CX - ROAD_W // 2 - 8, CY + ROAD_W // 2), 4
    )
    pygame.draw.line(
        screen, WHITE,
        (CX + ROAD_W // 2 + 8, CY - ROAD_W // 2),
        (CX + ROAD_W // 2 + 8, CY - 5), 4
    )


def draw_signal(x, y, color):
    pygame.draw.rect(screen, DARK, (x, y, 32, 65), border_radius=6)
    for i, c in enumerate([RED, YELLOW, GREEN]):
        radius = 7
        pygame.draw.circle(screen, c, (x + 16, y + 13 + i * 20), radius)
        if c == color:
            pygame.draw.circle(
                screen, WHITE, (x + 16, y + 13 + i * 20), radius, 2
            )


def draw_dashboard(phase, remaining, vehicles, spawned, passed, density, paused,
                   active_mode, requested_mode):
    panel = pygame.Rect(12, 12, 330, 190)
    pygame.draw.rect(screen, DARK, panel, border_radius=10)

    title = font.render("AUTONOMOUS TRAFFIC", True, WHITE)
    screen.blit(title, (24, 22))

    mode = "NORTH-SOUTH" if phase.startswith("NS") else "EAST-WEST"
    signal_color = GREEN if remaining > YELLOW_TIME else YELLOW
    phase_label = "ALL RED" if phase == "ALL_RED" else mode

    lines = [
        f"Active phase: {phase_label}",
        f"Time remaining: {remaining:.1f}s",
        f"Controller: {active_mode.upper()}" + (" (pending)" if active_mode != requested_mode else ""),
        f"Vehicles on road: {len(vehicles)}",
        f"Vehicles spawned: {spawned}",
        f"Vehicles passed: {passed}",
        f"Traffic density: {density}",
    ]

    for i, line in enumerate(lines):
        text = small_font.render(line, True, WHITE)
        screen.blit(text, (24, 53 + i * 17))

    status = "PAUSED" if paused else "RUNNING"
    screen.blit(font.render(status, True, signal_color), (WIDTH - 125, 18))

    help_text = small_font.render(
        "SPACE: Pause | R: Reset | UP/DOWN: Density | ESC: Quit",
        True, WHITE
    )
    screen.blit(help_text, (20, HEIGHT - 28))


def main():
    vehicles = []
    phase = "NS"
    phase_time = 0.0
    green_duration = GREEN_TIME
    last_green_phase = "NS"
    active_mode = "fixed"
    requested_mode = "fixed"
    command_revision = 0
    applied_revision = 0
    simulation_control_revision = 0
    reset_revision = 0
    time_since_green = {"NS": 0.0, "EW": 0.0}
    spawn_timer = 0.0
    density = 3
    paused = False
    spawned = 0
    passed = 0
    running = True
    telemetry = TelemetryClient()
    controller = AdaptiveSignalController(fixed_green_seconds=GREEN_TIME)
    simulation_id = str(uuid.uuid4())
    telemetry.start()
    next_phase_preview, next_phase_in_seconds = next_green_preview(
        phase, phase_time, green_duration, YELLOW_TIME, ALL_RED_TIME,
        last_green_phase, active_mode,
    )
    telemetry.report_controller_state({
        "active_mode": active_mode,
        "phase": phase,
        "green_duration": green_duration,
        "phase_duration_seconds": green_duration,
        "phase_elapsed_seconds": phase_time,
        "next_phase_preview": next_phase_preview,
        "next_phase_in_seconds": next_phase_in_seconds,
        "command_revision": applied_revision,
        "simulation_id": simulation_id,
        "paused": paused,
    })

    try:
        while running:
            dt = clock.tick(60) / 1000.0
            for command in telemetry.drain_commands():
                requested_mode = command.get("requested_mode", requested_mode)
                command_revision = max(command_revision, int(command.get("revision", 0)))

            for command in telemetry.drain_simulation_commands():
                revision = int(command.get("revision", simulation_control_revision))
                if revision > simulation_control_revision:
                    paused = bool(command.get("paused", paused))
                    density = max(1, min(10, int(command.get("density", density))))
                    simulation_control_revision = revision
                requested_reset = int(command.get("reset_revision", reset_revision))
                if requested_reset > reset_revision:
                    vehicles.clear()
                    spawn_timer = 0
                    spawned = 0
                    passed = 0
                    reset_revision = requested_reset

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_SPACE:
                        paused = not paused
                    elif event.key == pygame.K_UP:
                        density = min(10, density + 1)
                    elif event.key == pygame.K_DOWN:
                        density = max(1, density - 1)
                    elif event.key == pygame.K_r:
                        vehicles.clear()
                        spawn_timer = 0
                        spawned = 0
                        passed = 0

            if not paused:
                phase_time += dt
                for axis in ("NS", "EW"):
                    if phase == axis:
                        time_since_green[axis] = 0.0
                    else:
                        time_since_green[axis] += dt

                # Every phase change passes through yellow and an all-red
                # clearance interval. Only this main thread mutates signals.
                next_phase = clearance_transition(
                    phase, phase_time, green_duration, YELLOW_TIME, ALL_RED_TIME
                )
                if next_phase in ("NS_YELLOW", "EW_YELLOW", "ALL_RED"):
                    phase = next_phase
                    phase_time = 0.0
                elif next_phase == "GREEN_BOUNDARY":
                    active_mode = requested_mode
                    applied_revision = command_revision
                    snapshot = build_simulation_metrics(
                        vehicles, passed, phase, density, CX, CY, ROAD_W, CAR_H, active_mode
                    )
                    decision_payload = None
                    if active_mode == "adaptive":
                        decision = controller.decide(snapshot, last_green_phase, time_since_green)
                        phase = decision.selected_phase
                        green_duration = decision.green_duration
                        decision_payload = decision.as_dict()
                    else:
                        phase = "EW" if last_green_phase == "NS" else "NS"
                        green_duration = GREEN_TIME
                    last_green_phase = phase
                    time_since_green[phase] = 0.0
                    phase_time = 0.0
                    next_phase_preview, next_phase_in_seconds = next_green_preview(
                        phase, phase_time, green_duration, YELLOW_TIME, ALL_RED_TIME,
                        last_green_phase, active_mode,
                    )
                    applied_report = {
                        "active_mode": active_mode,
                        "phase": phase,
                        "green_duration": green_duration,
                        "phase_duration_seconds": green_duration,
                        "phase_elapsed_seconds": phase_time,
                        "next_phase_preview": next_phase_preview,
                        "next_phase_in_seconds": next_phase_in_seconds,
                        "command_revision": applied_revision,
                        "simulation_id": simulation_id,
                        "simulation_control_revision": simulation_control_revision,
                        "paused": paused,
                        "density": density,
                        "reset_revision": reset_revision,
                    }
                    if decision_payload is not None:
                        applied_report["decision"] = decision_payload
                    telemetry.report_controller_state(applied_report)

                # More density means vehicles arrive more frequently.
                spawn_timer += dt
                interval = max(0.25, SPAWN_INTERVAL / (density / 3))

                if spawn_timer >= interval:
                    spawn_timer = 0
                    direction = random.choice(DIRECTIONS)

                    # Avoid spawning a new vehicle directly on top
                    # of another vehicle at the same lane entrance.
                    entrance_clear = True
                    for v in vehicles:
                        if v.direction != direction:
                            continue
                        if direction == "down" and v.y < 55:
                            entrance_clear = False
                        elif direction == "up" and v.y > HEIGHT - 55:
                            entrance_clear = False
                        elif direction == "right" and v.x < 55:
                            entrance_clear = False
                        elif direction == "left" and v.x > WIDTH - 55:
                            entrance_clear = False

                    if entrance_clear:
                        vehicles.append(Vehicle(direction))
                        spawned += 1

                green_axis = permitted_green_axis(phase)
                ns_allowed = green_axis == "NS"
                ew_allowed = green_axis == "EW"

                for vehicle in vehicles:
                    allowed = (
                        ns_allowed if vehicle.direction in ("down", "up")
                        else ew_allowed
                    )
                    vehicle.update(vehicles, allowed, dt)

                # Remove vehicles that have left the screen.
                remaining = []
                for v in vehicles:
                    outside = (
                        v.y > HEIGHT + 60 or v.y < -60
                        or v.x > WIDTH + 60 or v.x < -60
                    )
                    if outside:
                        passed += 1
                    else:
                        remaining.append(v)
                vehicles = remaining

            # Make a small immutable snapshot on the Pygame thread; HTTP runs
            # exclusively in TelemetryClient's background worker.
            telemetry.offer(build_simulation_metrics(
                vehicles, passed, phase, density, CX, CY, ROAD_W, CAR_H, active_mode
            ))
            next_phase_preview, next_phase_in_seconds = next_green_preview(
                phase, phase_time, green_duration, YELLOW_TIME, ALL_RED_TIME,
                last_green_phase, active_mode,
            )
            telemetry.report_controller_state({
                "active_mode": active_mode,
                "phase": phase,
                "green_duration": green_duration,
                "phase_duration_seconds": (
                    green_duration if phase in ("NS", "EW")
                    else YELLOW_TIME if phase.endswith("_YELLOW")
                    else ALL_RED_TIME
                ),
                "phase_elapsed_seconds": phase_time,
                "next_phase_preview": next_phase_preview,
                "next_phase_in_seconds": next_phase_in_seconds,
                "command_revision": applied_revision,
                "simulation_id": simulation_id,
                "simulation_control_revision": simulation_control_revision,
                "paused": paused,
                "density": density,
                "reset_revision": reset_revision,
            })

            draw_road()

            # Signal indicators beside each approach
            ns_color = (
                GREEN if phase == "NS"
                else YELLOW if phase == "NS_YELLOW"
                else RED
            )
            ew_color = (
                GREEN if phase == "EW"
                else YELLOW if phase == "EW_YELLOW"
                else RED
            )

            draw_signal(CX - 92, CY - 158, ns_color)
            draw_signal(CX + 60, CY + 92, ns_color)
            draw_signal(CX + 92, CY - 92, ew_color)
            draw_signal(CX - 158, CY + 28, ew_color)

            for vehicle in vehicles:
                vehicle.draw(screen)

            if phase in ("NS", "EW"):
                remaining_time = green_duration - phase_time
            elif phase in ("NS_YELLOW", "EW_YELLOW"):
                remaining_time = YELLOW_TIME - phase_time
            else:
                remaining_time = ALL_RED_TIME - phase_time

            draw_dashboard(
                phase, max(0, remaining_time), vehicles,
                spawned, passed, density, paused, active_mode, requested_mode
            )

            pygame.display.flip()
    finally:
        telemetry.close()
        pygame.quit()


if __name__ == "__main__":
    main()
