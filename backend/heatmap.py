"""
LuminaVenue — Heatmap Engine
============================
Simulates crowd density per venue node using phase-aware Gaussian profiles.

Mathematical model
------------------
Density is sampled from N(μ, σ²) via the Box-Muller transform:

    Z = sqrt(-2 * ln(U1)) * cos(2π * U2)   where U1, U2 ~ Uniform(0,1)
    X = μ + σ * Z

μ and σ are defined per (node_type × game_phase) in PHASE_DENSITY_PROFILES.
The raw sample is clamped to [0.0, 1.0].

Usage
-----
    engine = HeatmapEngine(node_meta=graph.all_nodes())
    density_map = engine.simulate_tick("halftime")
    payload     = engine.get_heatmap_payload()
"""

import math
import random

NodeId     = str
DensityMap = dict[NodeId, float]

# (μ, σ) per node_type per game phase
PHASE_DENSITY_PROFILES: dict[str, dict[str, tuple[float, float]]] = {
    "pre_game": {
        "gate":             (0.78, 0.09),
        "concourse":        (0.42, 0.08),
        "seating":          (0.22, 0.06),
        "amenity_food":     (0.52, 0.11),
        "amenity_restroom": (0.32, 0.08),
    },
    "in_play": {
        "gate":             (0.08, 0.04),
        "concourse":        (0.14, 0.05),
        "seating":          (0.88, 0.04),
        "amenity_food":     (0.18, 0.07),
        "amenity_restroom": (0.12, 0.05),
    },
    "halftime": {
        "gate":             (0.05, 0.03),
        "concourse":        (0.82, 0.09),
        "seating":          (0.28, 0.10),
        "amenity_food":     (0.92, 0.06),
        "amenity_restroom": (0.88, 0.07),
    },
    "in_play_q3": {
        "gate":             (0.05, 0.03),
        "concourse":        (0.19, 0.05),
        "seating":          (0.84, 0.04),
        "amenity_food":     (0.24, 0.07),
        "amenity_restroom": (0.18, 0.05),
    },
    "final_whistle": {
        "gate":             (0.62, 0.14),
        "concourse":        (0.91, 0.07),
        "seating":          (0.38, 0.14),
        "amenity_food":     (0.28, 0.09),
        "amenity_restroom": (0.48, 0.10),
    },
    "egress": {
        "gate":             (0.88, 0.08),
        "concourse":        (0.72, 0.09),
        "seating":          (0.09, 0.04),
        "amenity_food":     (0.14, 0.07),
        "amenity_restroom": (0.18, 0.07),
    },
}

_DEFAULT_PROFILE: tuple[float, float] = (0.20, 0.05)


# ---------------------------------------------------------------------------
# Mathematical primitives (no scipy — manual implementation)
# ---------------------------------------------------------------------------

def gaussian_pdf(x: float, mu: float, sigma: float) -> float:
    """Evaluate the Gaussian PDF at x.

    f(x) = 1/(σ√(2π)) * exp(-(x-μ)²/(2σ²))
    """
    if sigma <= 0.0:
        return 0.0
    coefficient = 1.0 / (sigma * math.sqrt(2.0 * math.pi))
    exponent    = -((x - mu) ** 2) / (2.0 * sigma ** 2)
    return coefficient * math.exp(exponent)


def sample_gaussian(mu: float, sigma: float, rng: random.Random) -> float:
    """Draw one sample from N(μ, σ²) via the Box-Muller transform.

        U1, U2 ~ Uniform(0,1)
        Z = sqrt(-2*ln(U1)) * cos(2π*U2)
        X = μ + σ*Z
    """
    if sigma <= 0.0:
        return mu

    u1 = rng.random()
    u2 = rng.random()

    if u1 < 1e-12:
        u1 = 1e-12

    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z


# ---------------------------------------------------------------------------
# HeatmapEngine
# ---------------------------------------------------------------------------

class HeatmapEngine:
    """Simulates per-node crowd density using phase-aware Gaussian profiles."""

    def __init__(self, node_meta: list[dict], seed: int = 42) -> None:
        self._nodes: list[dict]        = node_meta
        self._last_density: DensityMap = {}
        self._last_phase: str          = ""
        self._rng: random.Random       = random.Random(seed)

    def simulate_tick(self, phase: str) -> DensityMap:
        """Sample density for every node from its phase-specific Gaussian profile.

        Returns:
            node_id → density float in [0.0, 1.0]. Empty dict on invalid phase.
        """
        if phase not in PHASE_DENSITY_PROFILES:
            return {}

        if not self._nodes:
            return {}

        profile      = PHASE_DENSITY_PROFILES[phase]
        density_map: DensityMap = {}

        for node in self._nodes:
            node_id   = node.get("id", "")
            node_type = node.get("type", "")

            if not node_id:
                continue

            mu, sigma = profile.get(node_type, _DEFAULT_PROFILE)
            raw       = sample_gaussian(mu, sigma, self._rng)
            density_map[node_id] = round(max(0.0, min(1.0, raw)), 4)

        self._last_density = density_map
        self._last_phase   = phase
        return density_map

    def get_heatmap_payload(self) -> list[dict]:
        """Return GeoJSON-compatible payload for the Google Maps HeatmapLayer.

        Each entry carries lat, lng, and weight (density) expected by the API.
        Returns empty list if simulate_tick() has not been called yet.
        """
        if not self._last_density:
            return []

        meta_index: dict[str, dict] = {
            node["id"]: node for node in self._nodes if "id" in node
        }

        payload: list[dict] = []

        for node_id, density in self._last_density.items():
            meta = meta_index.get(node_id)

            if meta is None:
                continue

            payload.append({
                "node_id": node_id,
                "label":   meta.get("label", node_id),
                "type":    meta.get("type", "unknown"),
                "lat":     meta.get("lat", 0.0),
                "lng":     meta.get("lng", 0.0),
                "density": density,
                "weight":  density,
            })

        return payload

    def density_level(self, node_id: NodeId) -> str:
        """Classify a node's density: 'low' | 'moderate' | 'high' | 'critical'."""
        density = self._last_density.get(node_id)

        if density is None:
            return "unknown"
        if density < 0.40:
            return "low"
        if density < 0.70:
            return "moderate"
        if density < 0.85:
            return "high"
        return "critical"

    def quiet_nodes(self, node_type: str | None = None) -> list[dict]:
        """Return nodes with density < 0.40, optionally filtered by type."""
        meta_index: dict[str, dict] = {
            node["id"]: node for node in self._nodes if "id" in node
        }
        results: list[dict] = []

        for node_id, density in self._last_density.items():
            if density >= 0.40:
                continue

            meta = meta_index.get(node_id, {})

            if node_type is not None and meta.get("type") != node_type:
                continue

            results.append({
                "node_id": node_id,
                "label":   meta.get("label", node_id),
                "density": density,
            })

        return sorted(results, key=lambda n: n["density"])

    def reseed(self, seed: int) -> None:
        """Reseed the internal RNG for deterministic test runs."""
        self._rng = random.Random(seed)

    @property
    def last_phase(self) -> str:
        return self._last_phase

    @property
    def last_density(self) -> DensityMap:
        return dict(self._last_density)
