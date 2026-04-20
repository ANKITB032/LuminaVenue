"""
LuminaVenue — Smart Queue Estimator Unit Tests
===============================================
Tests for QueueEstimator, poisson_sample, _format_wait,
and _congestion_level — covering mathematical boundaries,
type contracts, phase-sensitivity, and utility helpers.

Run with:
    pytest tests/test_queue.py -v
"""

import random

import pytest

from backend.smart_queue import (
    QueueEstimator,
    QUEUE_NODES,
    MAX_WAIT_S,
    poisson_sample,
    _format_wait,
    _congestion_level,
)


# ---------------------------------------------------------------------------
# 1. poisson_sample — mathematical boundary tests
# ---------------------------------------------------------------------------

def test_poisson_sample_returns_non_negative_int() -> None:
    """Every Poisson sample must be a non-negative integer."""
    rng = random.Random(0)
    for _ in range(200):
        result = poisson_sample(5.0, rng)
        assert isinstance(result, int)
        assert result >= 0


def test_poisson_sample_zero_lambda_returns_zero() -> None:
    """λ=0 means no arrivals — must always return 0."""
    rng = random.Random(0)
    for _ in range(50):
        assert poisson_sample(0.0, rng) == 0


def test_poisson_sample_negative_lambda_returns_zero() -> None:
    """Negative λ is nonsensical — must return 0 (guard clause)."""
    rng = random.Random(0)
    assert poisson_sample(-5.0, rng) == 0
    assert poisson_sample(-0.001, rng) == 0


def test_poisson_sample_large_lambda_returns_int() -> None:
    """Very large λ uses the direct approximation path — must still be int."""
    rng = random.Random(42)
    result = poisson_sample(800.0, rng)
    assert isinstance(result, int)
    assert result > 0


def test_poisson_sample_mean_approximates_lambda() -> None:
    """Over many samples, E[X] ≈ λ (law of large numbers check)."""
    rng    = random.Random(99)
    lam    = 10.0
    n      = 5_000
    total  = sum(poisson_sample(lam, rng) for _ in range(n))
    mean   = total / n
    # Allow ±15% tolerance — tight enough to catch implementation bugs.
    assert abs(mean - lam) / lam < 0.15, (
        f"Poisson mean {mean:.2f} deviates too far from λ={lam}"
    )


def test_poisson_sample_unit_lambda() -> None:
    """λ=1 should yield samples mostly in {0, 1, 2, 3}."""
    rng     = random.Random(7)
    samples = [poisson_sample(1.0, rng) for _ in range(500)]
    assert all(s >= 0 for s in samples)
    # Virtually all mass of Poisson(1) is within 0–6.
    assert all(s < 20 for s in samples)


# ---------------------------------------------------------------------------
# 2. estimate_one — single node wait time
# ---------------------------------------------------------------------------

def test_estimate_one_valid_node_and_phase() -> None:
    """estimate_one must return a non-negative integer for valid inputs."""
    est  = QueueEstimator(seed=42)
    wait = est.estimate_one("restroom_w", "halftime")
    assert isinstance(wait, int)
    assert wait >= 0


def test_estimate_one_capped_at_max_wait() -> None:
    """estimate_one must never exceed MAX_WAIT_S."""
    est = QueueEstimator(seed=0)
    for phase in ["halftime", "final_whistle", "egress", "pre_game"]:
        for node_id in QUEUE_NODES:
            wait = est.estimate_one(node_id, phase)
            assert wait <= MAX_WAIT_S, (
                f"Wait {wait}s exceeds MAX_WAIT_S={MAX_WAIT_S} "
                f"for {node_id} @ {phase}"
            )


def test_estimate_one_unknown_node_returns_zero() -> None:
    """An untracked node must return 0 — no crash."""
    est = QueueEstimator(seed=42)
    assert est.estimate_one("parking_lot_b", "halftime") == 0
    assert est.estimate_one("", "in_play") == 0


def test_estimate_one_unknown_phase_returns_zero() -> None:
    """An invalid phase must return 0 — no crash."""
    est = QueueEstimator(seed=42)
    assert est.estimate_one("restroom_w", "extra_time") == 0
    assert est.estimate_one("food_court_n", "") == 0


# ---------------------------------------------------------------------------
# 3. estimate_all — bulk estimation
# ---------------------------------------------------------------------------

def test_estimate_all_returns_all_queue_nodes() -> None:
    """estimate_all must return an entry for every node in QUEUE_NODES."""
    est    = QueueEstimator(seed=42)
    result = est.estimate_all("halftime")

    for node_id in QUEUE_NODES:
        assert node_id in result, f"Missing node '{node_id}' in estimate_all result."
        assert isinstance(result[node_id], int)
        assert result[node_id] >= 0


def test_estimate_all_empty_on_invalid_phase() -> None:
    """An invalid phase must return an empty dict."""
    est    = QueueEstimator(seed=42)
    result = est.estimate_all("invalid_phase")
    assert result == {}


def test_estimate_all_updates_last_map() -> None:
    """After estimate_all(), last_map must match the returned dict."""
    est    = QueueEstimator(seed=42)
    result = est.estimate_all("in_play")
    assert est.last_map == result


def test_estimate_all_in_play_returns_positive_values() -> None:
    """Even the lowest-activity phase must produce non-negative waits."""
    est = QueueEstimator(seed=1)
    result = est.estimate_all("in_play")
    for node_id, wait in result.items():
        assert wait >= 0, f"{node_id} returned negative wait: {wait}"


# ---------------------------------------------------------------------------
# 4. Phase sensitivity — halftime must be busier than in_play
# ---------------------------------------------------------------------------

def test_halftime_aggregate_wait_exceeds_in_play() -> None:
    """Aggregate wait across all nodes must be higher at halftime vs in_play.

    λ values at halftime are 5–10× those at in_play, so the cumulative
    Poisson mean guarantees this holds across enough RNG seeds.
    """
    halftime_totals: list[int] = []
    in_play_totals:  list[int] = []

    for seed in range(30):
        est = QueueEstimator(seed=seed)
        halftime_totals.append(sum(est.estimate_all("halftime").values()))

        est.reseed(seed)
        in_play_totals.append(sum(est.estimate_all("in_play").values()))

    assert sum(halftime_totals) > sum(in_play_totals), (
        "Expected halftime aggregate wait > in_play aggregate wait across 30 seeds."
    )


def test_final_whistle_busier_than_pre_game() -> None:
    """final_whistle λ > pre_game λ for restrooms — mean waits should reflect this."""
    totals: dict[str, int] = {"pre_game": 0, "final_whistle": 0}

    for seed in range(20):
        for phase in totals:
            est = QueueEstimator(seed=seed)
            totals[phase] += sum(est.estimate_all(phase).values())

    assert totals["final_whistle"] > totals["pre_game"]


# ---------------------------------------------------------------------------
# 5. queue_summary
# ---------------------------------------------------------------------------

def test_queue_summary_is_sorted_descending() -> None:
    """queue_summary must return nodes ordered by wait_seconds descending."""
    est     = QueueEstimator(seed=7)
    summary = est.queue_summary("halftime")

    assert len(summary) > 0
    for i in range(len(summary) - 1):
        assert summary[i]["wait_seconds"] >= summary[i + 1]["wait_seconds"], (
            f"Sort violation at index {i}: "
            f"{summary[i]['wait_seconds']} < {summary[i+1]['wait_seconds']}"
        )


def test_queue_summary_has_required_fields() -> None:
    """Every summary entry must contain all expected keys."""
    est      = QueueEstimator(seed=42)
    summary  = est.queue_summary("pre_game")
    required = {"node_id", "phase", "wait_seconds", "wait_label", "congestion_level"}

    for entry in summary:
        assert required.issubset(entry.keys()), (
            f"Summary entry missing keys: {required - entry.keys()}"
        )


def test_queue_summary_empty_on_invalid_phase() -> None:
    """An invalid phase must return an empty summary list."""
    est     = QueueEstimator(seed=0)
    summary = est.queue_summary("sudden_death")
    assert summary == []


# ---------------------------------------------------------------------------
# 6. _format_wait — formatting utility
# ---------------------------------------------------------------------------

def test_format_wait_zero_seconds() -> None:
    assert _format_wait(0) == "0:00"


def test_format_wait_negative_is_zero() -> None:
    assert _format_wait(-10) == "0:00"


def test_format_wait_exactly_one_minute() -> None:
    assert _format_wait(60) == "1:00"


def test_format_wait_ninety_seconds() -> None:
    assert _format_wait(90) == "1:30"


def test_format_wait_nine_seconds_pad() -> None:
    """Single-digit seconds must be zero-padded (e.g., 1:05 not 1:5)."""
    assert _format_wait(65) == "1:05"


def test_format_wait_ten_minutes() -> None:
    assert _format_wait(600) == "10:00"


# ---------------------------------------------------------------------------
# 7. _congestion_level — classification boundaries
# ---------------------------------------------------------------------------

def test_congestion_level_low_boundary() -> None:
    assert _congestion_level(0)   == "low"
    assert _congestion_level(119) == "low"


def test_congestion_level_moderate_boundary() -> None:
    assert _congestion_level(120) == "moderate"
    assert _congestion_level(299) == "moderate"


def test_congestion_level_high_boundary() -> None:
    assert _congestion_level(300) == "high"
    assert _congestion_level(599) == "high"


def test_congestion_level_critical_boundary() -> None:
    assert _congestion_level(600)  == "critical"
    assert _congestion_level(3600) == "critical"


def test_congestion_level_all_values_are_valid_strings() -> None:
    """_congestion_level must return one of the four expected strings."""
    valid = {"low", "moderate", "high", "critical"}
    for wait_s in [0, 50, 120, 200, 300, 500, 600, 1000, 3600]:
        level = _congestion_level(wait_s)
        assert level in valid, f"Unexpected level '{level}' for wait={wait_s}s"


# ---------------------------------------------------------------------------
# 8. reseed determinism
# ---------------------------------------------------------------------------

def test_reseed_produces_identical_estimates() -> None:
    """Two QueueEstimators with the same seed must produce identical results."""
    est_a = QueueEstimator(seed=123)
    est_b = QueueEstimator(seed=123)

    result_a = est_a.estimate_all("halftime")
    result_b = est_b.estimate_all("halftime")

    assert result_a == result_b


def test_different_seeds_produce_different_estimates() -> None:
    """Different seeds should (with overwhelming probability) differ."""
    est_a = QueueEstimator(seed=1)
    est_b = QueueEstimator(seed=9999)

    result_a = est_a.estimate_all("halftime")
    result_b = est_b.estimate_all("halftime")

    # At least one node's estimate must differ.
    any_diff = any(
        result_a[nid] != result_b[nid] for nid in QUEUE_NODES
    )
    assert any_diff, "Suspiciously identical results from different seeds."
