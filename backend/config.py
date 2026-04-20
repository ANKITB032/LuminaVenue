"""
LuminaVenue — Configuration & Constants
TODO: Add role-based access control (RBAC) for staff vs. fan roles.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Google API ---
GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "YOUR_KEY_HERE")

# --- Venue Config ---
VENUE_NAME: str = "LuminaArena"
VENUE_CAPACITY: int = 55_000
VENUE_GRAPH_PATH: str = "frontend/assets/venue_layout.json"

# --- Simulation Tuning ---
DENSITY_UPDATE_INTERVAL_S: int = 30   # seconds between density recalculations
QUEUE_AVG_SERVICE_TIME_S: int = 45    # avg seconds per person served

# --- Routing Weights ---
ALPHA: float = 1.5   # density factor weight
BETA: float = 2.0    # queue penalty weight
GAMMA_PHASES: dict = {
    "pre_game": 1.0,
    "in_play": 1.2,
    "halftime": 2.5,
    "in_play_q3": 1.3,
    "final_whistle": 3.0,
    "egress": 2.8,
}

# --- Alert Templates ---
ALERT_TEMPLATES: dict = {
    "halftime":      "⚡ Halftime! Use Gate C & D — lowest wait times now.",
    "final_whistle": "🏟️ Match over! Exit via North Concourse for fastest egress.",
    "pre_game":      "🎉 Gates open! Concession queues shortest at Stands 12 & 34.",
    "egress":        "🚗 Parking lots A2 & B1 are clearest. Shuttle running on Loop 3.",
}
