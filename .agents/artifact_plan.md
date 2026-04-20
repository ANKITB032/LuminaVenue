# LuminaVenue — Backend Artifact Build Plan

> **Convention:** No `try/except` blocks anywhere in the Python codebase.
> All error handling via explicit `if/else` conditional guards.

---

## Build Sequence

### ✅ Phase 0 — Foundation (Complete)
| File | Status | Notes |
|---|---|---|
| `backend/config.py` | ✅ Done | Constants, weights, phase modifiers, alert templates |
| `frontend/assets/venue_layout.json` | ✅ Done | 14-node synthetic stadium graph |

---

### 🔄 Phase 1 — Graph Engine (Current)
| File | Status | Depends On |
|---|---|---|
| `backend/graph_engine.py` | ✅ Done | `config.py`, `venue_layout.json` |

**Exports:**
- `VenueGraph` class
- `VenueGraph.load(path)` — builds NetworkX DiGraph from JSON
- `VenueGraph.update_weights(density_map, queue_map, phase)` — recomputes `W(e)`
- `VenueGraph.find_quiet_route(source, target)` — Dijkstra, returns path + total cost
- `VenueGraph.node_density(node_id)` — returns current float density for a node

---

### Phase 2 — Density Simulation
| File | Status | Depends On |
|---|---|---|
| `backend/heatmap.py` | ⬜ Pending | `graph_engine.py`, `config.py` |

**Exports:**
- `HeatmapEngine` class
- `HeatmapEngine.simulate_tick(phase)` → `dict[node_id, float]` density map
- `HeatmapEngine.get_heatmap_payload()` → GeoJSON-compatible list for Maps overlay
- Uses `numpy` Gaussian distribution seeded on historical ingress curves per phase.

---

### Phase 3 — Smart Queue Estimator
| File | Status | Depends On |
|---|---|---|
| `backend/smart_queue.py` | ⬜ Pending | `config.py` |

**Exports:**
- `QueueEstimator` class
- `QueueEstimator.estimate_all()` → `dict[node_id, int]` wait seconds per amenity node
- `QueueEstimator.estimate_one(node_id)` → `int` wait seconds
- Poisson arrival model fallback; optional Google Places API hook via `httpx`.
- Queue nodes: `restroom_w`, `restroom_e`, `food_court_n`, `concession_s`

---

### Phase 4 — Alert Engine
| File | Status | Depends On |
|---|---|---|
| `backend/alert_engine.py` | ⬜ Pending | `config.py` |

**Exports:**
- `AlertEngine` class (singleton state machine)
- `AlertEngine.set_phase(phase: str)` — validates phase, updates state
- `AlertEngine.get_current_alert()` → `dict` with `phase`, `message`, `timestamp`
- `AlertEngine.phase_sequence` — ordered tuple defining valid transitions
- SSE-compatible: yields JSON-serialisable dicts consumed by `main.py` event stream.

---

### Phase 5 — Firebase Stub
| File | Status | Depends On |
|---|---|---|
| `backend/firebase_stub.py` | ⬜ Pending | None |

**Exports:**
- `FirebaseStub` class mirroring Firebase RTDB API contract
- `FirebaseStub.set(path, data)` — writes to in-memory dict store
- `FirebaseStub.get(path)` → value at path or `None`
- `FirebaseStub.on_value(path, callback)` — registers listener (polling stub)
- Swap: set `FIREBASE_LIVE=true` in `.env` to activate real `firebase-admin` SDK.

---

### Phase 6 — FastAPI Application
| File | Status | Depends On |
|---|---|---|
| `backend/main.py` | ⬜ Pending | All above modules |

**Routes:**
| Method | Path | Handler | Notes |
|---|---|---|---|
| `GET` | `/api/route` | `graph_engine` | Params: `from`, `to`, `phase` |
| `GET` | `/api/heatmap` | `heatmap` | Returns GeoJSON node density list |
| `GET` | `/api/queue` | `smart_queue` | Returns all queue wait times |
| `GET` | `/api/alerts/stream` | `alert_engine` | SSE stream, `text/event-stream` |
| `POST` | `/api/alerts/trigger` | `alert_engine` | Body: `{"phase": "halftime"}` |
| `GET` | `/health` | inline | Returns `{"status": "ok"}` |

---

### Phase 7 — Tests
| File | Status | Depends On |
|---|---|---|
| `tests/test_graph.py` | ⬜ Pending | `graph_engine.py` |
| `tests/test_queue.py` | ⬜ Pending | `smart_queue.py` |

**Test coverage targets:**
- Quiet route returns valid node-id path list
- Composite weights change correctly with phase modifier
- Queue estimator returns positive integers
- Alert engine rejects invalid phase transitions

---

### Phase 8 — Frontend
| File | Status | Depends On |
|---|---|---|
| `frontend/index.html` | ⬜ Pending | All API routes live |
| `frontend/map.html` | ⬜ Pending | Google Maps API key |

**Frontend features:**
- TailwindCSS CDN (dark theme)
- Live heatmap overlay on Google Maps canvas
- Quiet Route panel with turn-by-turn node path
- Smart Queue wait-time cards (auto-refreshed every 30s)
- SSE alert banner (top of screen, dismissible)
- Game phase control panel (staff view, POST to `/api/alerts/trigger`)

---

## Coding Conventions

| Convention | Rule |
|---|---|
| Error handling | `if/else` guards only — **no `try/except`** |
| Imports | stdlib → third-party → local, one blank line between groups |
| Docstrings | Module-level + every public method/class |
| Type hints | All function signatures fully annotated |
| Constants | Imported from `config.py` — no magic numbers inline |
| Naming | `snake_case` for functions/vars, `PascalCase` for classes |
