"""
LuminaVenue — Graph Engine
==========================
Models the venue as a weighted directed graph G = (V, E).

Composite edge weight formula:
    W(e) = base_distance + α·density_factor + β·queue_penalty + γ·event_phase_modifier

Where:
    α  = ALPHA  (config.py) — crowd density weight
    β  = BETA   (config.py) — queue penalty weight
    γ  = GAMMA_PHASES[phase] (config.py) — event phase modifier

Usage:
    graph = VenueGraph()
    graph.load("frontend/assets/venue_layout.json")
    graph.update_weights(density_map, queue_map, "halftime")
    result = graph.find_quiet_route("gate_a", "section_200")
"""

import json
from pathlib import Path
from typing import Optional

import networkx as nx

from backend.config import ALPHA, BETA, GAMMA_PHASES


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
NodeId = str
DensityMap = dict[NodeId, float]   # node_id → density  (0.0 – 1.0)
QueueMap = dict[NodeId, int]       # node_id → wait seconds


class VenueGraph:
    """Weighted directed graph representation of the venue layout.

    The graph is built once from ``venue_layout.json`` and then kept
    up-to-date via :meth:`update_weights` calls driven by the heatmap
    and queue engines (every ``DENSITY_UPDATE_INTERVAL_S`` seconds).
    """

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._node_meta: dict[NodeId, dict] = {}
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, layout_path: str) -> None:
        """Load venue graph from a JSON layout file.

        Args:
            layout_path: Relative or absolute path to ``venue_layout.json``.

        Raises:
            FileNotFoundError: If the layout file does not exist.
            ValueError: If the JSON is missing required keys.
        """
        path = Path(layout_path)

        if not path.exists():
            raise FileNotFoundError(
                f"Venue layout not found: {path.resolve()}"
            )

        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)

        if "nodes" not in data or "edges" not in data:
            raise ValueError(
                "venue_layout.json must contain 'nodes' and 'edges' keys."
            )

        self._build_graph(data["nodes"], data["edges"])
        self._loaded = True

    def update_weights(
        self,
        density_map: DensityMap,
        queue_map: QueueMap,
        phase: str,
    ) -> None:
        """Recompute composite edge weights from live density, queue, and phase data.

        Args:
            density_map: Mapping of node_id → density float (0.0–1.0).
            queue_map:   Mapping of node_id → queue wait time in seconds.
            phase:       Current game phase key (must exist in GAMMA_PHASES).
        """
        if not self._loaded:
            raise RuntimeError(
                "Graph has not been loaded. Call load() before update_weights()."
            )

        if phase not in GAMMA_PHASES:
            raise ValueError(
                f"Unknown phase '{phase}'. Valid phases: {list(GAMMA_PHASES.keys())}"
            )

        gamma = GAMMA_PHASES[phase]

        for u, v, edge_data in self._graph.edges(data=True):
            base = edge_data["base_distance"]

            density_u = density_map.get(u, 0.0)
            density_v = density_map.get(v, 0.0)
            avg_density = (density_u + density_v) / 2.0

            queue_u = queue_map.get(u, 0)
            queue_v = queue_map.get(v, 0)
            max_queue = max(queue_u, queue_v)

            composite = (
                base
                + ALPHA * avg_density * 100   # scale density (0–1) to metre-equivalent
                + BETA * (max_queue / 60.0)   # convert seconds → minute-equivalent units
                + gamma * base * 0.1          # phase modifier as 10 % base surcharge
            )

            self._graph[u][v]["weight"] = round(composite, 4)

    def find_quiet_route(
        self,
        source: NodeId,
        target: NodeId,
    ) -> dict:
        """Find the lowest-cost path from source to target using Dijkstra's algorithm.

        Returns a result dict with:
            - ``path``        : ordered list of node IDs
            - ``total_cost``  : sum of composite edge weights along path
            - ``node_labels`` : human-readable label for each node in path
            - ``valid``       : True if a route was found, False otherwise

        Args:
            source: Starting node ID (e.g., ``"gate_a"``).
            target: Destination node ID (e.g., ``"section_200"``).
        """
        if not self._loaded:
            return _error_result("Graph not loaded.")

        if source not in self._graph:
            return _error_result(f"Source node '{source}' not in graph.")

        if target not in self._graph:
            return _error_result(f"Target node '{target}' not in graph.")

        if source == target:
            return {
                "valid": True,
                "path": [source],
                "total_cost": 0.0,
                "node_labels": [self._node_meta[source]["label"]],
                "message": "Already at destination.",
            }

        if not nx.has_path(self._graph, source, target):
            return _error_result(
                f"No path exists between '{source}' and '{target}'."
            )

        path: list[NodeId] = nx.dijkstra_path(
            self._graph, source, target, weight="weight"
        )
        total_cost: float = nx.dijkstra_path_length(
            self._graph, source, target, weight="weight"
        )

        node_labels = [
            self._node_meta[node_id]["label"]
            for node_id in path
            if node_id in self._node_meta
        ]

        return {
            "valid": True,
            "path": path,
            "total_cost": round(total_cost, 2),
            "node_labels": node_labels,
            "message": "Quiet route found.",
        }

    def node_density(self, node_id: NodeId) -> float:
        """Return the last-set density for a node, or 0.0 if not tracked.

        Args:
            node_id: The node to query.
        """
        if node_id not in self._graph:
            return 0.0

        return self._graph.nodes[node_id].get("density", 0.0)

    def set_node_density(self, node_id: NodeId, density: float) -> None:
        """Store density on a node for later retrieval via :meth:`node_density`.

        Args:
            node_id: Target node.
            density: Float in range [0.0, 1.0].
        """
        if node_id not in self._graph:
            return

        clamped = max(0.0, min(1.0, density))
        self._graph.nodes[node_id]["density"] = clamped

    def all_nodes(self) -> list[dict]:
        """Return node metadata list (id, label, type, lat, lng, capacity)."""
        return [
            {
                "id": node_id,
                **meta,
                "density": self._graph.nodes[node_id].get("density", 0.0),
            }
            for node_id, meta in self._node_meta.items()
        ]

    def all_edges(self) -> list[dict]:
        """Return edge list with current composite weights."""
        return [
            {
                "source": u,
                "target": v,
                "base_distance": data.get("base_distance", 0),
                "weight": data.get("weight", data.get("base_distance", 0)),
            }
            for u, v, data in self._graph.edges(data=True)
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_graph(
        self,
        nodes: list[dict],
        edges: list[dict],
    ) -> None:
        """Construct the NetworkX DiGraph from raw JSON node and edge lists."""
        self._graph.clear()
        self._node_meta.clear()

        for node in nodes:
            node_id = node["id"]
            meta = {
                "label":    node.get("label", node_id),
                "type":     node.get("type", "unknown"),
                "capacity": node.get("capacity", 0),
                "lat":      node.get("lat", 0.0),
                "lng":      node.get("lng", 0.0),
            }
            self._node_meta[node_id] = meta
            self._graph.add_node(node_id, density=0.0, **meta)

        for edge in edges:
            source = edge["source"]
            target = edge["target"]
            base   = edge.get("base_distance", 100)
            bidir  = edge.get("bidirectional", False)

            self._graph.add_edge(source, target, base_distance=base, weight=base)

            if bidir:
                self._graph.add_edge(target, source, base_distance=base, weight=base)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _error_result(message: str) -> dict:
    """Construct a standardised error result dict (no exception raised)."""
    return {
        "valid": False,
        "path": [],
        "total_cost": 0.0,
        "node_labels": [],
        "message": message,
    }
