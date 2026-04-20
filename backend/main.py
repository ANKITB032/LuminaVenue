"""
LuminaVenue — FastAPI Application
===================================
All API routes for the LuminaVenue smart venue assistant.

Routes
------
GET  /health                  → Service health check
GET  /api/route               → Quiet route (Dijkstra on live weights)
GET  /api/heatmap             → Node density map (GeoJSON payload)
GET  /api/queue               → Queue wait-time summary
GET  /api/alerts/stream       → Server-Sent Event alert stream
POST /api/alerts/trigger      → Manually advance the game phase

Design notes
------------
- VenueGraph, HeatmapEngine, QueueEstimator, AlertEngine, and FirebaseStub
  are instantiated once as module-level singletons and shared across requests.
- /api/route explicitly calls update_weights() with fresh heatmap and queue
  data before executing Dijkstra's algorithm, ensuring live weights.
- All validation is done via if/else + raise HTTPException.
  No try/except blocks are used anywhere in this file.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.alert_engine import AlertEngine, PHASE_SEQUENCE
from backend.config import GAMMA_PHASES, VENUE_GRAPH_PATH
from backend.firebase_stub import FirebaseStub
from backend.graph_engine import VenueGraph
from backend.heatmap import HeatmapEngine
from backend.smart_queue import QueueEstimator


# ---------------------------------------------------------------------------
# Singletons — created at module load, initialised during lifespan startup.
# ---------------------------------------------------------------------------

graph:         VenueGraph     = VenueGraph()
heatmap_engine: HeatmapEngine | None = None   # requires node_meta from graph
queue_est:     QueueEstimator = QueueEstimator(seed=42)
alert_engine:  AlertEngine    = AlertEngine()
firebase:      FirebaseStub   = FirebaseStub()

_STARTUP_PHASE: str = PHASE_SEQUENCE[0]   # "pre_game"


# ---------------------------------------------------------------------------
# Lifespan — replaces deprecated @app.on_event("startup")
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Load the venue graph and seed Firebase state on startup."""
    global heatmap_engine

    layout_path = Path(VENUE_GRAPH_PATH)

    if not layout_path.exists():
        raise RuntimeError(
            f"Venue layout file not found: {layout_path.resolve()}. "
            "Run from the project root so relative paths resolve correctly."
        )

    graph.load(VENUE_GRAPH_PATH)

    # HeatmapEngine requires node metadata from the loaded graph.
    heatmap_engine = HeatmapEngine(node_meta=graph.all_nodes(), seed=42)

    # Seed Firebase with initial state.
    firebase.set("venue/phase", _STARTUP_PHASE)
    firebase.set("venue/name",  "LuminaArena")
    firebase.set("venue/capacity", 55_000)

    # Prime the first density tick so weights are non-zero from request 1.
    density_map = heatmap_engine.simulate_tick(_STARTUP_PHASE)
    queue_map   = queue_est.estimate_all(_STARTUP_PHASE)
    graph.update_weights(density_map, queue_map, _STARTUP_PHASE)

    firebase.set("venue/heatmap", heatmap_engine.get_heatmap_payload())
    firebase.set("venue/queue",   queue_map)

    yield  # ← application runs here

    # Teardown (nothing needed for this demo).


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="LuminaVenue API",
    description=(
        "Smart venue assistant — real-time crowd routing, "
        "density heatmaps, queue estimation, and game-phase alerts."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helper: guard that heatmap_engine is initialised
# ---------------------------------------------------------------------------

def _require_heatmap() -> HeatmapEngine:
    """Return heatmap_engine or raise 503 if startup has not completed."""
    if heatmap_engine is None:
        raise HTTPException(
            status_code=503,
            detail="Service warming up — heatmap engine not yet initialised.",
        )
    return heatmap_engine


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
def health() -> dict:
    """Quick liveness check — no dependencies."""
    return {
        "status":       "ok",
        "phase":        alert_engine.current_phase,
        "graph_loaded": graph._loaded,
    }


@app.get("/api/route", tags=["routing"])
def get_quiet_route(
    source: str = Query(..., description="Starting node ID, e.g. gate_a"),
    target: str = Query(..., description="Destination node ID, e.g. section_200"),
    phase:  str = Query(default="in_play", description="Current game phase"),
) -> dict:
    """Return the quiet (lowest composite cost) route from source to target.

    Workflow:
        1. Validate parameters.
        2. Run a heatmap tick + queue estimation for the requested phase.
        3. Call update_weights() to push fresh data into the graph.
        4. Run Dijkstra and return the result.
    """
    if not source or not source.strip():
        raise HTTPException(status_code=400, detail="'source' query param is required.")

    if not target or not target.strip():
        raise HTTPException(status_code=400, detail="'target' query param is required.")

    if phase not in GAMMA_PHASES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown phase '{phase}'. Valid: {list(GAMMA_PHASES.keys())}",
        )

    engine = _require_heatmap()

    # Always refresh weights with latest simulation data before routing.
    density_map = engine.simulate_tick(phase)
    queue_map   = queue_est.estimate_all(phase)
    graph.update_weights(density_map, queue_map, phase)

    # Sync live state to Firebase stub.
    firebase.set("venue/heatmap", engine.get_heatmap_payload())
    firebase.set("venue/queue",   queue_map)

    result = graph.find_quiet_route(source, target)

    if not result["valid"]:
        raise HTTPException(status_code=404, detail=result["message"])

    return {
        "source":       source,
        "target":       target,
        "phase":        phase,
        "route":        result,
        "density_snap": {nid: density_map.get(nid, 0.0) for nid in result["path"]},
        "queue_snap":   {nid: queue_map.get(nid, 0)     for nid in result["path"]},
    }


@app.get("/api/heatmap", tags=["heatmap"])
def get_heatmap(
    phase: str = Query(default="in_play", description="Current game phase"),
) -> dict:
    """Return per-node density data as a GeoJSON-compatible payload.

    Each node entry carries ``lat``, ``lng``, and ``weight`` for use with
    the Google Maps JavaScript API HeatmapLayer.
    """
    if phase not in GAMMA_PHASES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown phase '{phase}'. Valid: {list(GAMMA_PHASES.keys())}",
        )

    engine = _require_heatmap()

    density_map = engine.simulate_tick(phase)
    payload     = engine.get_heatmap_payload()

    # Sync to Firebase.
    firebase.set("venue/heatmap", payload)

    # Update graph node density attributes for introspection.
    for node_id, density in density_map.items():
        graph.set_node_density(node_id, density)

    return {
        "phase":    phase,
        "count":    len(payload),
        "nodes":    payload,
        "quiet":    engine.quiet_nodes(),
    }


@app.get("/api/queue", tags=["queue"])
def get_queue(
    phase: str = Query(default="in_play", description="Current game phase"),
) -> dict:
    """Return wait-time estimates for all tracked amenity nodes.

    Response includes per-node wait seconds, a formatted mm:ss label,
    and a congestion level badge string.
    """
    if phase not in GAMMA_PHASES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown phase '{phase}'. Valid: {list(GAMMA_PHASES.keys())}",
        )

    summary  = queue_est.queue_summary(phase)
    wait_map = queue_est.last_map

    # Sync to Firebase.
    firebase.set("venue/queue", wait_map)

    return {
        "phase":   phase,
        "summary": summary,
    }


@app.post("/api/alerts/trigger", tags=["alerts"])
def trigger_alert(body: dict) -> dict:
    """Advance the game phase and push the corresponding alert.

    Body:
        ``{"phase": "halftime"}``

    Returns the transition result dict. If the transition is rejected
    (invalid phase, backwards move), a 400 is raised with the reason.
    """
    phase = body.get("phase", "")

    if not phase or not isinstance(phase, str):
        raise HTTPException(
            status_code=400,
            detail="Request body must be JSON with a 'phase' string field.",
        )

    if phase not in GAMMA_PHASES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown phase '{phase}'. Valid: {list(GAMMA_PHASES.keys())}",
        )

    result = alert_engine.set_phase(phase)

    if result["transition"] == "rejected":
        raise HTTPException(status_code=400, detail=result["reason"])

    # Mirror new phase state into Firebase.
    firebase.set("venue/phase",        phase)
    firebase.set("venue/alert",        result)
    firebase.set("venue/phase_modifier", result["phase_modifier"])

    return result


@app.get("/api/alerts/stream", tags=["alerts"])
async def alert_stream() -> StreamingResponse:
    """Server-Sent Event stream of game-phase alerts.

    Clients subscribe to this endpoint to receive live alert updates.
    Each event is a JSON-encoded alert payload emitted every 3 seconds.

    SSE format:
        data: {"phase": "halftime", "message": "...", ...}\\n\\n
    """

    async def _event_generator() -> AsyncGenerator[str, None]:
        """Async generator — yields SSE-formatted strings indefinitely."""
        last_phase = ""

        while True:
            payload    = alert_engine.get_current_alert()
            phase_now  = payload.get("phase", "")
            event_json = json.dumps(payload)

            # Always send on phase change; also send a heartbeat every cycle.
            yield f"data: {event_json}\n\n"

            if phase_now != last_phase:
                firebase.set("venue/alert", payload)
                last_phase = phase_now

            await asyncio.sleep(3.0)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":  "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/graph/nodes", tags=["graph"])
def get_graph_nodes() -> dict:
    """Return all venue nodes with current density values (debug / map overlay)."""
    return {"nodes": graph.all_nodes()}


@app.get("/api/graph/edges", tags=["graph"])
def get_graph_edges() -> dict:
    """Return all venue edges with current composite weights (debug)."""
    return {"edges": graph.all_edges()}
