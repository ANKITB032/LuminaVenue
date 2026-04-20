"""
LuminaVenue — Firebase Realtime Database Stub
=============================================
Mirrors the Firebase Realtime Database API contract using a nested
in-memory dictionary as the local document store.

Design goals
------------
- **Zero-friction swap**: Set ``FIREBASE_LIVE=true`` in ``.env`` to
  replace this stub with the real ``firebase-admin`` SDK — no call-site
  changes required.
- **NoSQL semantics**: Paths are ``/``-separated strings (e.g.
  ``"venue/nodes/gate_a/density"``). Nested dicts are created on
  ``set()`` and traversed on ``get()``.
- **Listener model**: ``on_value(path, callback)`` registers a watcher
  that is invoked synchronously after any ``set()`` or ``delete()``
  touching that path or any of its descendants.
- **No try/except**: All path parsing, lookup, and write operations
  are guarded with ``if/else`` conditionals.

Usage
-----
    db = FirebaseStub()
    db.set("venue/nodes/gate_a/density", 0.72)
    db.set("venue/phase", "halftime")

    density = db.get("venue/nodes/gate_a/density")  # → 0.72
    phase   = db.get("venue/phase")                 # → "halftime"
    missing = db.get("venue/nodes/fake/x")          # → None

    db.on_value("venue/nodes/gate_a", lambda v: print("changed:", v))
    db.delete("venue/nodes/gate_a/density")

    snapshot = db.snapshot()                         # full store copy
"""

import time
import uuid
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
PathStr  = str
JsonVal  = Any          # str | int | float | bool | dict | list | None
Callback = Callable[[JsonVal], None]


# ---------------------------------------------------------------------------
# FirebaseStub
# ---------------------------------------------------------------------------

class FirebaseStub:
    """In-memory NoSQL document store mirroring the Firebase RTDB API.

    Attributes:
        _store:     Root nested-dict document store.
        _listeners: Registered ``on_value`` watchers.
                    Structure: listener_id → {"path": str, "cb": Callback}
    """

    def __init__(self) -> None:
        self._store:     dict           = {}
        self._listeners: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Core read / write
    # ------------------------------------------------------------------

    def set(self, path: PathStr, data: JsonVal) -> bool:
        """Write ``data`` at ``path``, creating intermediate nodes.

        Args:
            path: ``/``-separated key path, e.g. ``"venue/phase"``.
            data: Any JSON-serialisable value.

        Returns:
            True on success; False if ``path`` is empty or invalid.
        """
        keys = _parse_path(path)

        if not keys:
            return False

        node = self._store

        for key in keys[:-1]:
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]

        node[keys[-1]] = data
        self._notify_listeners(path, data)
        return True

    def get(self, path: PathStr) -> JsonVal:
        """Read the value at ``path``.

        Args:
            path: ``/``-separated key path.

        Returns:
            The stored value, or ``None`` if path does not exist or is invalid.
        """
        keys = _parse_path(path)

        if not keys:
            return None

        node = self._store

        for key in keys:
            if not isinstance(node, dict):
                return None

            if key not in node:
                return None

            node = node[key]

        return node

    def delete(self, path: PathStr) -> bool:
        """Delete the value (and all descendants) at ``path``.

        Args:
            path: ``/``-separated key path.

        Returns:
            True if the key existed and was deleted; False otherwise.
        """
        keys = _parse_path(path)

        if not keys:
            return False

        node = self._store

        for key in keys[:-1]:
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]

        if not isinstance(node, dict) or keys[-1] not in node:
            return False

        del node[keys[-1]]
        self._notify_listeners(path, None)
        return True

    def update(self, path: PathStr, data: dict) -> bool:
        """Shallow-merge ``data`` into the dict at ``path``.

        Mirrors Firebase's ``update()`` semantics: only top-level keys of
        ``data`` are written; existing sibling keys are preserved.

        Args:
            path: Path to an existing or new dict node.
            data: Dict of key/value pairs to merge.

        Returns:
            True on success; False if ``data`` is not a dict or path invalid.
        """
        if not isinstance(data, dict):
            return False

        keys = _parse_path(path)

        if not keys:
            return False

        node = self._store

        for key in keys:
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]

        for field, value in data.items():
            node[field] = value

        self._notify_listeners(path, node)
        return True

    def push(self, path: PathStr, data: JsonVal) -> str:
        """Append ``data`` under ``path`` with an auto-generated unique key.

        Mirrors Firebase's ``push()`` — creates a list-like structure of
        uniquely-keyed children. The generated key is a UUID4 string.

        Args:
            path: Parent path.
            data: Value to append.

        Returns:
            The generated child key, or empty string on failure.
        """
        child_key = uuid.uuid4().hex[:20]
        child_path = f"{path.rstrip('/')}/{child_key}"

        success = self.set(child_path, data)

        if not success:
            return ""

        return child_key

    # ------------------------------------------------------------------
    # Listener API (on_value / off)
    # ------------------------------------------------------------------

    def on_value(self, path: PathStr, callback: Callback) -> str:
        """Register a callback to be invoked whenever ``path`` changes.

        The callback receives the new value at ``path`` (or ``None`` on
        deletion). It is also invoked immediately with the current value.

        Args:
            path:     Path to watch.
            callback: Callable accepting one argument (the new value).

        Returns:
            A listener ID string for use with ``off()``.
            Returns empty string if path or callback is invalid.
        """
        if not path or not callable(callback):
            return ""

        listener_id = uuid.uuid4().hex

        self._listeners[listener_id] = {
            "path": path.strip("/"),
            "cb":   callback,
        }

        # Fire immediately with current value (Firebase behaviour)
        current = self.get(path)
        callback(current)

        return listener_id

    def off(self, listener_id: str) -> bool:
        """Deregister a listener by its ID.

        Args:
            listener_id: ID returned by ``on_value()``.

        Returns:
            True if removed, False if not found.
        """
        if listener_id not in self._listeners:
            return False

        del self._listeners[listener_id]
        return True

    # ------------------------------------------------------------------
    # Utility / introspection
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a shallow copy of the entire store for inspection."""
        return dict(self._store)

    def exists(self, path: PathStr) -> bool:
        """Return True if a value (including None) is set at ``path``."""
        keys = _parse_path(path)

        if not keys:
            return False

        node = self._store

        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]

        return True

    def list_children(self, path: PathStr) -> list[str]:
        """Return the child keys of the dict node at ``path``.

        Args:
            path: Path to a dict node.

        Returns:
            List of child key strings, or empty list if path is missing
            or the node is not a dict.
        """
        node = self.get(path)

        if not isinstance(node, dict):
            return []

        return list(node.keys())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _notify_listeners(self, changed_path: PathStr, new_value: JsonVal) -> None:
        """Invoke all listeners whose registered path is a prefix of (or
        equal to) ``changed_path``.

        A listener at ``"venue/nodes"`` fires when ``"venue/nodes/gate_a/density"``
        changes, matching Firebase's subtree-watch semantics.
        """
        normalised = changed_path.strip("/")

        for listener_id, entry in self._listeners.items():
            watch_path = entry["path"]
            callback   = entry["cb"]

            is_match = (
                watch_path == normalised
                or normalised.startswith(watch_path + "/")
                or watch_path.startswith(normalised + "/")
            )

            if is_match and callable(callback):
                # Deliver the value at the *listener's* registered path
                delivery_value = self.get(watch_path)
                callback(delivery_value)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _parse_path(path: PathStr) -> list[str]:
    """Split a ``/``-separated path string into a list of non-empty keys.

    Args:
        path: e.g. ``"/venue/nodes/gate_a/"`` → ``["venue", "nodes", "gate_a"]``

    Returns:
        List of key strings. Returns empty list for blank or invalid input.
    """
    if not path or not isinstance(path, str):
        return []

    parts = [p for p in path.strip("/").split("/") if p]
    return parts
