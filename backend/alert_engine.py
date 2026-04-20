"""
LuminaVenue — Alert Engine
==========================
A deterministic phase-based state machine that drives context-aware
crowd alerts throughout the game lifecycle.

Phase sequence (enforced — no skipping, no reverse):
    pre_game → in_play → halftime → in_play_q3 → final_whistle → egress

State machine rules
-------------------
- Only phases defined in ``config.GAMMA_PHASES`` are valid.
- Transitions must follow the order in ``PHASE_SEQUENCE``.
- Skipping phases or going backwards is rejected via ``if/else`` guards.
- Each valid transition appends an immutable entry to ``_history``.
- ``get_current_alert()`` always returns a JSON-serialisable dict.

Usage
-----
    engine = AlertEngine()
    result = engine.set_phase("halftime")
    alert  = engine.get_current_alert()

    # SSE usage in main.py:
    for payload in engine.alert_stream():
        yield payload   # send as Server-Sent Event
"""

import time
from typing import Generator

from backend.config import ALERT_TEMPLATES, GAMMA_PHASES


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Ordered valid phase progression — no reverse transitions allowed.
PHASE_SEQUENCE: tuple[str, ...] = (
    "pre_game",
    "in_play",
    "halftime",
    "in_play_q3",
    "final_whistle",
    "egress",
)

# Derived quick-lookup: phase → its position in the sequence.
_PHASE_INDEX: dict[str, int] = {
    phase: idx for idx, phase in enumerate(PHASE_SEQUENCE)
}

# Default alert for phases with no specific template in config.
_DEFAULT_ALERT_MSG: str = "🏟️ Stay aware of your surroundings and follow staff guidance."


# ---------------------------------------------------------------------------
# AlertEngine
# ---------------------------------------------------------------------------

class AlertEngine:
    """Deterministic game-phase state machine for crowd alert delivery.

    Only forward transitions along ``PHASE_SEQUENCE`` are accepted.
    All validation is done with ``if/else`` — no exceptions propagate
    to the caller for invalid input; instead a structured error dict
    is returned so the SSE stream never crashes.

    Attributes:
        _current_phase:  The active phase key string.
        _history:        Ordered list of all accepted phase transitions.
        _listeners:      Registered listener callbacks (id → callable).
    """

    def __init__(self) -> None:
        self._current_phase: str          = PHASE_SEQUENCE[0]
        self._history:       list[dict]   = []
        self._listeners:     dict[str, object] = {}

        # Record the initial phase as the first history entry.
        self._history.append(self._build_payload(PHASE_SEQUENCE[0], "initialised"))

    # ------------------------------------------------------------------
    # Core state-machine API
    # ------------------------------------------------------------------

    def set_phase(self, phase: str) -> dict:
        """Attempt a phase transition and return a result dict.

        Validates:
        1. ``phase`` is a recognised key in ``GAMMA_PHASES``.
        2. ``phase`` appears after the current phase in ``PHASE_SEQUENCE``
           (no backwards transitions, no same-phase re-triggers).

        Args:
            phase: Requested next phase key.

        Returns:
            On success — the alert payload dict (same shape as
            ``get_current_alert()``), with ``"transition": "accepted"``.
            On failure — an error dict with ``"transition": "rejected"``
            and a ``"reason"`` string. Always JSON-serialisable.
        """
        if phase not in GAMMA_PHASES:
            return _error_payload(
                phase=phase,
                reason=(
                    f"Unknown phase '{phase}'. "
                    f"Valid phases: {list(GAMMA_PHASES.keys())}"
                ),
            )

        current_idx  = _PHASE_INDEX.get(self._current_phase, -1)
        requested_idx = _PHASE_INDEX.get(phase, -1)

        if requested_idx <= current_idx:
            if requested_idx == current_idx:
                return _error_payload(
                    phase=phase,
                    reason=f"Phase '{phase}' is already active.",
                )
            return _error_payload(
                phase=phase,
                reason=(
                    f"Cannot transition from '{self._current_phase}' "
                    f"back to '{phase}'. Only forward transitions allowed."
                ),
            )

        # Valid transition — commit it.
        self._current_phase = phase
        payload = self._build_payload(phase, "accepted")
        self._history.append(payload)
        self._notify_listeners(payload)
        return payload

    def get_current_alert(self) -> dict:
        """Return the alert payload for the current active phase.

        Always returns a valid JSON-serialisable dict regardless of state.
        """
        return self._build_payload(self._current_phase, "active")

    def reset(self) -> dict:
        """Reset the state machine back to ``pre_game``.

        Clears history and listeners. Useful between test runs or events.

        Returns:
            The initial alert payload for ``pre_game``.
        """
        self._current_phase = PHASE_SEQUENCE[0]
        self._history       = []
        self._listeners     = {}
        payload = self._build_payload(PHASE_SEQUENCE[0], "reset")
        self._history.append(payload)
        return payload

    # ------------------------------------------------------------------
    # SSE stream generator
    # ------------------------------------------------------------------

    def alert_stream(
        self,
        poll_interval_s: float = 2.0,
        max_events: int = 0,
    ) -> Generator[dict, None, None]:
        """Yield alert payloads suitable for Server-Sent Events.

        Each yield is a JSON-serialisable dict. The caller (FastAPI route)
        formats it as ``data: <json>\n\n``.

        Args:
            poll_interval_s: Seconds between repeated yields of the same
                             phase (keeps the SSE connection alive).
            max_events:      Stop after this many events (0 = infinite).
                             Useful for testing.

        Yields:
            Alert payload dicts.
        """
        emitted = 0

        while True:
            payload = self.get_current_alert()
            yield payload
            emitted += 1

            if max_events > 0 and emitted >= max_events:
                return

            time.sleep(poll_interval_s)

    # ------------------------------------------------------------------
    # Listener registration (Firebase-compatible contract)
    # ------------------------------------------------------------------

    def on_phase_change(self, listener_id: str, callback: object) -> None:
        """Register a callback invoked on every accepted phase transition.

        Args:
            listener_id: Unique identifier for this listener.
            callback:    Callable accepting one dict (the alert payload).
        """
        if not listener_id:
            return

        self._listeners[listener_id] = callback

    def remove_listener(self, listener_id: str) -> bool:
        """Remove a registered listener.

        Args:
            listener_id: ID of the listener to remove.

        Returns:
            True if removed, False if not found.
        """
        if listener_id not in self._listeners:
            return False

        del self._listeners[listener_id]
        return True

    def _notify_listeners(self, payload: dict) -> None:
        """Invoke all registered listener callbacks with the new payload."""
        for listener_id, callback in self._listeners.items():
            if callable(callback):
                callback(payload)

    # ------------------------------------------------------------------
    # History & introspection
    # ------------------------------------------------------------------

    def phase_history(self) -> list[dict]:
        """Return an ordered copy of all accepted phase transition records."""
        return list(self._history)

    def valid_next_phases(self) -> list[str]:
        """Return the list of phases the engine can legally transition to."""
        current_idx = _PHASE_INDEX.get(self._current_phase, -1)
        return list(PHASE_SEQUENCE[current_idx + 1 :])

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_phase(self) -> str:
        """The currently active game phase key."""
        return self._current_phase

    @property
    def phase_modifier(self) -> float:
        """The γ weight modifier for the current phase (from config)."""
        return GAMMA_PHASES.get(self._current_phase, 1.0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_payload(self, phase: str, transition: str) -> dict:
        """Build a complete, JSON-serialisable alert payload dict."""
        message = ALERT_TEMPLATES.get(phase, _DEFAULT_ALERT_MSG)

        return {
            "phase":          phase,
            "transition":     transition,
            "message":        message,
            "phase_modifier": GAMMA_PHASES.get(phase, 1.0),
            "timestamp_utc":  round(time.time(), 3),
            "sequence_pos":   _PHASE_INDEX.get(phase, -1),
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _error_payload(phase: str, reason: str) -> dict:
    """Construct a standardised rejection payload (no exception raised)."""
    return {
        "phase":          phase,
        "transition":     "rejected",
        "message":        reason,
        "phase_modifier": None,
        "timestamp_utc":  round(time.time(), 3),
        "sequence_pos":   _PHASE_INDEX.get(phase, -1),
        "reason":         reason,
    }
