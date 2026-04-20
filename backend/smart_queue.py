"""
LuminaVenue — Smart Queue Estimator
====================================
Estimates wait times at amenity nodes (restrooms, food stands) using a
Poisson arrival model with Gaussian service-time jitter.

Mathematical model
------------------
Queue length is modelled as a Poisson random variable:

    Knuth's algorithm for Poisson sampling:
        L = e^(−λ)
        k = 0,  p = 1
        repeat: k += 1,  p *= U   where U ~ Uniform(0, 1)
        until p ≤ L
        queue_length = k − 1

    Wait time formula:
        wait_s = queue_length * AVG_SERVICE_TIME_S + Z * JITTER_SIGMA_S
        where Z ~ N(0,1) via Box-Muller,  clamped to [0, MAX_WAIT_S]

Arrival rates λ (people/min) are defined per (queue_node × game_phase)
in QUEUE_ARRIVAL_RATES.

Usage
-----
    estimator = QueueEstimator(seed=42)
    wait_map  = estimator.estimate_all("halftime")
    wait_secs = estimator.estimate_one("restroom_w", "halftime")
    summary   = estimator.queue_summary("halftime")
"""

import math
import random

from backend.config import QUEUE_AVG_SERVICE_TIME_S


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
NodeId   = str
WaitMap  = dict[NodeId, int]     # node_id → wait seconds


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
JITTER_SIGMA_S: int = 8          # ±8 s Gaussian jitter around the mean
MAX_WAIT_S:     int = 3600       # cap at 60 min (sanity bound)

# Queue nodes that the estimator tracks
QUEUE_NODES: tuple[str, ...] = (
    "food_court_n",
    "concession_s",
    "restroom_w",
    "restroom_e",
)

# Arrival rates λ (people arriving per minute) per phase per queue node.
# Higher λ → longer Poisson-sampled queue → longer wait.
QUEUE_ARRIVAL_RATES: dict[str, dict[NodeId, float]] = {
    "pre_game": {
        "food_court_n":  8.0,
        "concession_s":  6.0,
        "restroom_w":    4.0,
        "restroom_e":    4.0,
    },
    "in_play": {
        "food_court_n":  3.0,
        "concession_s":  2.0,
        "restroom_w":    2.0,
        "restroom_e":    2.0,
    },
    "halftime": {
        "food_court_n": 18.0,
        "concession_s": 14.0,
        "restroom_w":   20.0,
        "restroom_e":   20.0,
    },
    "in_play_q3": {
        "food_court_n":  4.0,
        "concession_s":  3.0,
        "restroom_w":    3.0,
        "restroom_e":    3.0,
    },
    "final_whistle": {
        "food_court_n":  6.0,
        "concession_s":  5.0,
        "restroom_w":    8.0,
        "restroom_e":    8.0,
    },
    "egress": {
        "food_court_n":  2.0,
        "concession_s":  2.0,
        "restroom_w":    3.0,
        "restroom_e":    3.0,
    },
}


# ---------------------------------------------------------------------------
# Mathematical primitives (manual — no scipy)
# ---------------------------------------------------------------------------

def poisson_sample(lam: float, rng: random.Random) -> int:
    """Draw one sample from a Poisson distribution using Knuth's algorithm.

    For a Poisson process with arrival rate λ:

        L = e^(−λ)
        k = 0,  p = 1
        Repeat:
            k = k + 1
            p = p * U    (U ~ Uniform(0,1))
        Until p ≤ L
        Return k − 1

    Args:
        lam: Arrival rate λ (must be > 0).
        rng: Seeded Random instance.

    Returns:
        Non-negative integer sample. Returns 0 for lam ≤ 0.
    """
    if lam <= 0.0:
        return 0

    # For large λ, e^(−λ) underflows to 0.0 — use log-space accumulation
    if lam > 700.0:
        return int(lam)   # expected value is λ itself; direct approximation

    L: float = math.exp(-lam)
    k: int   = 0
    p: float = 1.0

    while p > L:
        k += 1
        p *= rng.random()

    return k - 1


def _standard_normal(rng: random.Random) -> float:
    """Draw one Z ~ N(0,1) via the Box-Muller transform.

        U1, U2 ~ Uniform(0,1)
        Z = sqrt(-2*ln(U1)) * cos(2π*U2)
    """
    u1 = rng.random()
    u2 = rng.random()

    if u1 < 1e-12:
        u1 = 1e-12

    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


# ---------------------------------------------------------------------------
# QueueEstimator
# ---------------------------------------------------------------------------

class QueueEstimator:
    """Estimates wait times at amenity nodes using Poisson + Gaussian jitter.

    Attributes:
        _rng:        Seeded RNG for reproducible estimates.
        _last_map:   Most-recent wait-time map from ``estimate_all()``.
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng:      random.Random = random.Random(seed)
        self._last_map: WaitMap       = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate_one(self, node_id: NodeId, phase: str) -> int:
        """Estimate the queue wait time in seconds for a single amenity node.

        Formula:
            queue_length ~ Poisson(λ)
            wait_s = queue_length * AVG_SERVICE_TIME_S + round(Z * JITTER_SIGMA_S)
            wait_s = clamp(wait_s, 0, MAX_WAIT_S)

        Args:
            node_id: Amenity node to estimate (must be in QUEUE_NODES).
            phase:   Current game phase key.

        Returns:
            Estimated wait in seconds. Returns 0 for unknown node or phase.
        """
        if node_id not in QUEUE_NODES:
            return 0

        if phase not in QUEUE_ARRIVAL_RATES:
            return 0

        phase_rates = QUEUE_ARRIVAL_RATES[phase]
        lam         = phase_rates.get(node_id, 0.0)

        queue_length = poisson_sample(lam, self._rng)
        jitter       = round(_standard_normal(self._rng) * JITTER_SIGMA_S)
        raw_wait     = queue_length * QUEUE_AVG_SERVICE_TIME_S + jitter

        return max(0, min(MAX_WAIT_S, raw_wait))

    def estimate_all(self, phase: str) -> WaitMap:
        """Estimate wait times for all tracked queue nodes in one pass.

        Args:
            phase: Current game phase key.

        Returns:
            Mapping of node_id → wait seconds. Empty dict on invalid phase.
        """
        if phase not in QUEUE_ARRIVAL_RATES:
            return {}

        wait_map: WaitMap = {}

        for node_id in QUEUE_NODES:
            wait_map[node_id] = self.estimate_one(node_id, phase)

        self._last_map = wait_map
        return wait_map

    def queue_summary(self, phase: str) -> list[dict]:
        """Return a rich summary list sorted by wait time (descending).

        Each entry includes node_id, wait_seconds, wait_label (mm:ss),
        and a congestion_level string for the frontend badge.

        Args:
            phase: Current game phase key.

        Returns:
            List of summary dicts, longest queue first.
        """
        if phase not in QUEUE_ARRIVAL_RATES:
            return []

        wait_map = self.estimate_all(phase)
        summary:  list[dict] = []

        for node_id, wait_s in wait_map.items():
            summary.append({
                "node_id":         node_id,
                "phase":           phase,
                "wait_seconds":    wait_s,
                "wait_label":      _format_wait(wait_s),
                "congestion_level": _congestion_level(wait_s),
            })

        return sorted(summary, key=lambda e: e["wait_seconds"], reverse=True)

    def reseed(self, seed: int) -> None:
        """Reseed the internal RNG for deterministic test runs."""
        self._rng = random.Random(seed)

    @property
    def last_map(self) -> WaitMap:
        """Read-only snapshot of the most recent estimate_all() result."""
        return dict(self._last_map)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _format_wait(seconds: int) -> str:
    """Convert raw seconds to a human-readable mm:ss label."""
    if seconds <= 0:
        return "0:00"
    minutes = seconds // 60
    secs    = seconds % 60
    return f"{minutes}:{secs:02d}"


def _congestion_level(wait_s: int) -> str:
    """Map wait time to a UI congestion badge level.

    Thresholds:
        low      → < 2 min
        moderate → 2–5 min
        high     → 5–10 min
        critical → ≥ 10 min
    """
    if wait_s < 120:
        return "low"
    if wait_s < 300:
        return "moderate"
    if wait_s < 600:
        return "high"
    return "critical"
