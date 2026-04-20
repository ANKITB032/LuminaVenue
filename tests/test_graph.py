"""
LuminaVenue — Graph Engine Unit Tests
======================================
Tests for VenueGraph: graph loading, composite weight calculations,
Dijkstra quiet-route logic, and boundary/error-path validation.

Run with:
    pytest tests/test_graph.py -v
"""

import pytest
from pathlib import Path

from backend.graph_engine import VenueGraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LAYOUT_PATH = "frontend/assets/venue_layout.json"


def loaded_graph() -> VenueGraph:
    """Return a freshly loaded VenueGraph for each test that needs one."""
    g = VenueGraph()
    g.load(LAYOUT_PATH)
    return g


def _all_zero_density(graph: VenueGraph) -> dict:
    return {node["id"]: 0.0 for node in graph.all_nodes()}


def _uniform_density(graph: VenueGraph, value: float) -> dict:
    return {node["id"]: value for node in graph.all_nodes()}


# ---------------------------------------------------------------------------
# 1. Graph loading
# ---------------------------------------------------------------------------

def test_graph_loads_without_error() -> None:
    """load() must succeed for the canonical venue_layout.json."""
    g = VenueGraph()
    g.load(LAYOUT_PATH)
    assert g._loaded is True


def test_graph_node_count() -> None:
    """Venue layout should contain exactly 14 nodes."""
    g = loaded_graph()
    assert len(g.all_nodes()) == 14


def test_graph_node_ids_unique() -> None:
    """Every node must have a unique id."""
    g = loaded_graph()
    ids = [n["id"] for n in g.all_nodes()]
    assert len(ids) == len(set(ids))


def test_graph_nodes_have_required_fields() -> None:
    """Each node dict must carry id, label, type, capacity, lat, lng."""
    g = loaded_graph()
    required = {"id", "label", "type", "capacity", "lat", "lng"}
    for node in g.all_nodes():
        assert required.issubset(node.keys()), (
            f"Node '{node.get('id')}' is missing required fields."
        )


def test_graph_edges_present() -> None:
    """Graph must have at least one edge after loading."""
    g = loaded_graph()
    assert len(g.all_edges()) > 0


def test_graph_load_missing_file_raises() -> None:
    """load() with a non-existent path must raise FileNotFoundError."""
    g = VenueGraph()
    raised = False
    try:                                    # only try here — testing error path
        g.load("nonexistent/path/file.json")
    except FileNotFoundError:
        raised = True
    assert raised is True


# ---------------------------------------------------------------------------
# 2. Quiet-route — happy paths
# ---------------------------------------------------------------------------

def test_route_gate_a_to_section_200() -> None:
    """Gate A → Section 200 must return a valid multi-node path."""
    g = loaded_graph()
    result = g.find_quiet_route("gate_a", "section_200")

    assert result["valid"] is True
    assert result["path"][0] == "gate_a"
    assert result["path"][-1] == "section_200"
    assert len(result["path"]) >= 2
    assert result["total_cost"] > 0


def test_route_gate_b_to_restroom_w() -> None:
    """Gate B → Restroom West must find a path across the ring graph."""
    g = loaded_graph()
    result = g.find_quiet_route("gate_b", "restroom_w")

    assert result["valid"] is True
    assert len(result["path"]) >= 3   # must cross at least one concourse


def test_route_same_source_and_target() -> None:
    """A route from a node to itself must return zero cost with single-element path."""
    g = loaded_graph()
    result = g.find_quiet_route("gate_a", "gate_a")

    assert result["valid"] is True
    assert result["total_cost"] == 0.0
    assert result["path"] == ["gate_a"]


def test_route_returns_node_labels() -> None:
    """node_labels list must be same length as path list."""
    g = loaded_graph()
    result = g.find_quiet_route("gate_c", "food_court_n")

    assert result["valid"] is True
    assert len(result["node_labels"]) == len(result["path"])


# ---------------------------------------------------------------------------
# 3. Quiet-route — error / boundary paths
# ---------------------------------------------------------------------------

def test_route_invalid_source_returns_error_dict() -> None:
    """An unknown source node must return valid=False without raising."""
    g = loaded_graph()
    result = g.find_quiet_route("ghost_gate", "section_100")

    assert result["valid"] is False
    assert result["path"] == []
    assert result["total_cost"] == 0.0
    assert "ghost_gate" in result["message"]


def test_route_invalid_target_returns_error_dict() -> None:
    """An unknown target node must return valid=False without raising."""
    g = loaded_graph()
    result = g.find_quiet_route("gate_a", "ghost_section")

    assert result["valid"] is False
    assert result["path"] == []


def test_route_on_unloaded_graph_returns_error_dict() -> None:
    """find_quiet_route on an unloaded graph must return valid=False."""
    g = VenueGraph()   # NOT loaded
    result = g.find_quiet_route("gate_a", "section_200")

    assert result["valid"] is False
    assert "not loaded" in result["message"].lower()


# ---------------------------------------------------------------------------
# 4. Composite weight calculations
# ---------------------------------------------------------------------------

def test_update_weights_raises_on_invalid_phase() -> None:
    """update_weights with an unknown phase must raise ValueError."""
    g = loaded_graph()
    raised = False
    try:
        g.update_weights({}, {}, "overtime_shootout")
    except ValueError:
        raised = True
    assert raised is True


def test_halftime_costs_more_than_in_play() -> None:
    """Halftime (γ=2.5) must produce higher route cost than in_play (γ=1.2)."""
    g = loaded_graph()
    density = _uniform_density(g, 0.10)   # fixed low density

    g.update_weights(density, {}, "in_play")
    cost_in_play = g.find_quiet_route("gate_a", "section_100")["total_cost"]

    g.update_weights(density, {}, "halftime")
    cost_halftime = g.find_quiet_route("gate_a", "section_100")["total_cost"]

    assert cost_halftime > cost_in_play, (
        f"Expected halftime cost ({cost_halftime}) > in_play cost ({cost_in_play})"
    )


def test_egress_costs_more_than_in_play() -> None:
    """Egress (γ=2.8) must produce higher route cost than in_play (γ=1.2)."""
    g = loaded_graph()
    density = _uniform_density(g, 0.10)

    g.update_weights(density, {}, "in_play")
    cost_in_play = g.find_quiet_route("gate_b", "section_200")["total_cost"]

    g.update_weights(density, {}, "egress")
    cost_egress = g.find_quiet_route("gate_b", "section_200")["total_cost"]

    assert cost_egress > cost_in_play


def test_high_density_increases_route_cost() -> None:
    """Routing through a dense crowd must cost more than through a sparse crowd."""
    g = loaded_graph()
    phase = "in_play"

    low_density  = _uniform_density(g, 0.05)
    high_density = _uniform_density(g, 0.95)

    g.update_weights(low_density, {}, phase)
    cost_low = g.find_quiet_route("gate_a", "section_100")["total_cost"]

    g.update_weights(high_density, {}, phase)
    cost_high = g.find_quiet_route("gate_a", "section_100")["total_cost"]

    assert cost_high > cost_low, (
        f"High density cost ({cost_high}) should exceed low density cost ({cost_low})"
    )


def test_long_queue_increases_route_cost() -> None:
    """High queue penalties must increase route cost when path traverses amenity nodes."""
    g = loaded_graph()
    density = _all_zero_density(g)

    no_queue  = {}
    big_queue = {n["id"]: 600 for n in g.all_nodes()}   # 10-minute wait everywhere

    g.update_weights(density, no_queue, "in_play")
    cost_no_queue = g.find_quiet_route("gate_a", "section_100")["total_cost"]

    g.update_weights(density, big_queue, "in_play")
    cost_big_queue = g.find_quiet_route("gate_a", "section_100")["total_cost"]

    assert cost_big_queue > cost_no_queue


def test_all_edges_have_positive_weight_after_update() -> None:
    """Every edge weight must be > 0 after a valid update_weights call."""
    g = loaded_graph()
    density = _uniform_density(g, 0.30)
    g.update_weights(density, {}, "pre_game")

    for edge in g.all_edges():
        assert edge["weight"] > 0, (
            f"Edge {edge['source']} → {edge['target']} has non-positive weight."
        )


# ---------------------------------------------------------------------------
# 5. Node density utilities
# ---------------------------------------------------------------------------

def test_set_node_density_clamps_above_one() -> None:
    g = loaded_graph()
    g.set_node_density("gate_a", 1.8)
    assert g.node_density("gate_a") <= 1.0


def test_set_node_density_clamps_below_zero() -> None:
    g = loaded_graph()
    g.set_node_density("gate_a", -0.5)
    assert g.node_density("gate_a") >= 0.0


def test_node_density_returns_zero_for_unknown_node() -> None:
    g = loaded_graph()
    density = g.node_density("does_not_exist")
    assert density == 0.0
