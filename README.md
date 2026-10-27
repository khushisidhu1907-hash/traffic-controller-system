# Autonomous Traffic Management System

This repository keeps the existing Pygame intersection in the root `main.py` and adds a FastAPI service under `backend/`. The simulator creates categorized cars, motorcycles, and trucks, sends real telemetry to FastAPI, and supports fixed-time and explainable adaptive traffic-signal modes. The dashboard can pause/resume, reset vehicles/counters, and set traffic density in an already-running simulator; each change is applied on the Pygame main thread and shown as pending until acknowledged.

## Install and run (macOS)

From the project directory:

```sh
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

Run the Pygame simulation in a second terminal from the same directory:

```sh
source .venv/bin/activate
python main.py
```

The simulator tries to send a metrics snapshot to `http://127.0.0.1:8000/api/metrics` at the configured interval. Verify delivery at `GET http://127.0.0.1:8000/api/status` (`simulation_connected` becomes true only when a snapshot has arrived within the previous 15 seconds) or inspect `GET /api/metrics`. If the API is unavailable, the background worker logs a throttled warning and the Pygame loop continues.

The same background worker polls controller commands and sends simulator acknowledgements. Pygame remains the sole owner of live signal and vehicle state; FastAPI request threads never mutate it.

With the API running, open the dashboard at `http://127.0.0.1:8000/dashboard/`. It is served by FastAPI from `frontend/` and uses same-origin API requests, so no additional CORS origins are required. Use the sun/moon button in the top navigation to switch between light and dark themes; the selected theme is saved in the browser. Start `python main.py` in a second terminal for live simulator telemetry and controller status. The dashboard cannot launch Pygame; pause/resume, reset, and density controls require a connected simulator heartbeat. Reset clears vehicles and spawned/passed counters while retaining signal mode, density, and pause state. Vehicle category ratios are not configurable.

The Current Phase countdown is synchronized from the Pygame main loop through `/api/controller/state` and `/api/controller/status`. The simulator reports its current phase duration and elapsed simulation time, pause state, and next-green preview; the dashboard interpolates from the API receipt timestamp and freezes while paused. Controller state is reported four times per second and the dashboard refreshes that state every 500 ms so phase changes appear promptly. The dashboard does not advance or predict signal transitions independently. The supported phase sequence includes configured green intervals, 2-second yellow clearance, and 1-second all-red clearance. If simulator timing is missing or its heartbeat is stale, the countdown is replaced with a disconnected/stale message rather than a live-looking estimate.

## Configuration

- `DATABASE_URL` defaults to `sqlite:///./traffic.db`.
- `CORS_ORIGINS` defaults to `http://localhost:3000,http://localhost:5173`; provide a comma-separated explicit list for your frontend.
- `TRAFFIC_API_URL` defaults to `http://127.0.0.1:8000`.
- `TRAFFIC_TELEMETRY_INTERVAL` defaults to `1.0` seconds. This setting limits telemetry sampling and database growth.
- `TRAFFIC_API_TIMEOUT` defaults to `1.5` seconds per request.
- `TRAFFIC_MIN_GREEN_SECONDS` defaults to `5`; adaptive green durations cannot be shorter.
- `TRAFFIC_MAX_GREEN_SECONDS` defaults to `30`; adaptive green durations cannot be longer.
- `TRAFFIC_MAX_RED_SECONDS` defaults to `45`; an axis reaching this red time receives a fairness override.
- `LOG_LEVEL` defaults to `INFO`.

SQLite tables are created when the API starts. Timestamps are represented as ISO-8601 datetimes. The database stores simulation sessions, sampled metric snapshots, and submitted control decisions.

## API documentation

FastAPI interactive docs: `/docs`; ReDoc: `/redoc`; OpenAPI JSON: `/openapi.json`.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Project identity and API status |
| GET | `/api/health` | Health check |
| GET | `/api/status` | API status, telemetry freshness and last update |
| GET | `/api/simulation/status` | Simulator-confirmed state and pending command status |
| GET | `/api/simulation/command` | Command polled by the simulator worker |
| POST | `/api/simulation/start` | Resume a connected, paused simulator (HTTP 202) |
| POST | `/api/simulation/pause` | Pause the connected simulator (HTTP 202) |
| POST | `/api/simulation/reset` | Clear vehicles and spawn/pass counters (HTTP 202) |
| POST | `/api/simulation/density` | Set spawn density, integer 1–10 (HTTP 202) |
| GET | `/api/metrics` | Most recent telemetry; HTTP 503 until first submission |
| POST | `/api/metrics` | Validate and store a telemetry snapshot |
| GET | `/api/metrics/history` | Newest-first history; `limit` 1–1000 and `offset` pagination |
| POST | `/api/traffic-assistant` | Rule-based demo answers using stored telemetry and acknowledged controller state |
| GET | `/api/analytics/summary` | Aggregates calculated from stored snapshots |
| GET | `/api/analytics/performance` | Available recorded measurement counts/timestamps; no fabricated benchmarks |
| GET | `/api/analytics/export` | Download all recorded metric snapshots as CSV |
| POST | `/api/analytics/decisions` | Store a fixed/adaptive/ML-mode decision for future controller integration |
| GET | `/api/controller/status` | Requested/active mode, simulator connection, phase, and last decision |
| POST | `/api/controller/mode` | Request `fixed` or `adaptive`; Pygame applies it at a safe phase boundary |
| GET | `/api/controller/decision` | Latest decision actually produced and reported by the simulator |
| GET | `/api/controller/command` | Command channel polled by the simulator worker |
| POST | `/api/controller/state` | Simulator acknowledgement and actual decision reporting channel |
| GET | `/api/controller/performance` | Aggregate recorded telemetry by simulator-acknowledged mode |
| GET | `/dashboard/` | Responsive live traffic and controller dashboard |

### Traffic AI Assistant

The dashboard's floating assistant is a rule-based demo, not an external LLM or the traffic controller. It reads recorded simulator telemetry and simulator-acknowledged signal state. Its traffic-history response reports only trends in saved samples; no forecasting model is currently configured. Green-time responses are bounded advisory estimates and do not change signal timing. Emergency guidance is simulated and never sends a priority command. The API accepts `{"message":"Check congestion","action":"check_congestion"}` at `POST /api/traffic-assistant`; `action` is optional and may be `analyze_traffic`, `check_congestion`, `optimize_signals`, `predict_traffic`, or `emergency_mode`. Messages are limited to 1,000 characters.

Example metrics request:

```json
{
  "simulation_timestamp": "2026-10-18T12:00:00Z",
  "total_vehicles": 3,
  "motorcycle_count": 1,
  "car_count": 1,
  "truck_count": 1,
  "vehicles_passed": 15,
  "queues": {"north": 1, "south": 0, "east": 1, "west": 0},
  "average_waiting_time": 2.4,
  "maximum_queue_length": 1,
  "signal_phase": "NS",
  "traffic_density": 3
}
```

Control endpoints accept an optional JSON body such as `{"requested_by":"dashboard"}`. Density accepts `{"density":5}`. A decision request uses `selected_phase`, `reason`, `observed_queues`, `controller_mode` (`fixed`, `adaptive`, or `ml`), and optional positive `green_duration`. Recording mode `ml` does not imply that a model is implemented.

## Architecture and integration boundary

`backend/main.py` builds the FastAPI app and CORS configuration; `database.py` manages SQLAlchemy engines/sessions; `models.py` defines persistence; `schemas.py` validates inputs; `routes/` handles HTTP; and `services/` holds controller logic/state and metric persistence. `backend/telemetry_client.py` derives queues, category totals and waiting-time estimates from a main-thread snapshot, sends telemetry, polls controller and simulation commands, and reports acknowledgements from its worker. The HTTP worker never reads or mutates Pygame objects.

The controller is an explainable adaptive rule, not machine learning. It scores each axis from measured approach queues, directional waiting time, and directional vehicle/category counts. Empty or tied demand alternates axes; green duration is clamped to configured limits; a maximum-red override prevents starvation. Invalid or stale input falls back to a bounded fixed alternation. Phase changes always pass through yellow and a one-second all-red interval, and the opposing axis is never green concurrently. A requested mode is not reported active until Pygame acknowledges applying it at a safe boundary. Vehicle types are currently categorized and rendered by the existing renderer without distinct motorcycle/truck artwork. Queue estimates are position-based within 150 pixels of each approach stop line.

Request and verify adaptive mode while both server and Pygame are running:

```sh
curl -X POST http://127.0.0.1:8000/api/controller/mode \
  -H 'Content-Type: application/json' -d '{"mode":"adaptive"}'
curl http://127.0.0.1:8000/api/controller/status
curl http://127.0.0.1:8000/api/controller/decision
```

Use `{"mode":"fixed"}` to return to fixed-time alternation. Adaptive decisions are evaluated automatically at every safe phase boundary, so a separate manual-step endpoint is unnecessary. `/api/controller/performance` aggregates actual telemetry by active mode, including average waiting, average queue length per approach, maximum queue, and the last observed cumulative passed counter. That counter is not per-mode throughput; compare periods/runs with similar demand and duration, not as a controlled benchmark. Existing SQLite databases receive additive nullable columns for controller mode and recorded decision conditions at startup.

## Tests

Run the backend test suite with:

```sh
python -m pytest -q
```

Tests configure an isolated temporary SQLite database; they do not write to the project's normal `traffic.db`.
