"""
core/tactical.py
Pitch geography, tactical event flags, xT and progressive-distance scoring.

Adapted from ClipMaker (MIT, (c) 2026 B4L1) — see config/CLIPMAKER_LICENSE.
The xT grid (config/xT_Grid.csv) ships from the same project.

Why this exists
---------------
The scraper already pulls x / y / endX / endY for every event and, until v33,
did nothing with them. These helpers turn those coordinates into filters that
describe what actually happened — "passes into the box", "carries into the
final third", "switches of play" — rather than just which Opta type fired.
For finding clip-worthy moments that's a far better handle than "all passes".

Coordinate system
-----------------
WhoScored gives 0-100 on both axes, per-team normalised: x=0 is the player's
own goal line, x=100 the opponent's; y=0 to 100 across the pitch. Real-world
conversion assumes a 105m x 68m pitch, so metres = (x * 1.05, y * 0.68).

Deviation from ClipMaker (deliberate)
------------------------------------
ClipMaker's progressive pass/carry distance measures from raw 0-100 coordinates
to the point (105, 34) — mixing the 0-100 scale with metre constants, so its
"distance to goal" is not in any consistent unit. This module converts to
metres first, so `prog_distance` is genuinely metres gained toward goal and a
threshold like "10m" means what it says.
"""

import csv
import json
import math
import os

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

# Box: WhoScored 0-100 scale. x>83 is roughly the 18-yard line; 21<y<79 its width.
BOX_X = 83.0
BOX_Y_MIN, BOX_Y_MAX = 21.0, 79.0
FINAL_THIRD_X = 67.0


# ── Pitch geography ──────────────────────────────────────────────────────────

def in_box(x, y) -> bool:
    """True if the point is inside the attacking penalty area."""
    try:
        return float(x) > BOX_X and BOX_Y_MIN < float(y) < BOX_Y_MAX
    except (TypeError, ValueError):
        return False


def pitch_zone(y) -> str:
    """Lateral zone: Left Wing / Left Half Space / Centre / Right Half Space / Right Wing."""
    try:
        y = float(y)
    except (TypeError, ValueError):
        return ""
    if y < 20:
        return "Left Wing"
    if y < 37:
        return "Left Half Space"
    if y < 63:
        return "Centre"
    if y < 80:
        return "Right Half Space"
    return "Right Wing"


def depth_zone(x) -> str:
    """Depth zone: Defensive Third / Middle Third / Attacking Third."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ""
    if x < 34:
        return "Defensive Third"
    if x < FINAL_THIRD_X:
        return "Middle Third"
    return "Attacking Third"


def _to_metres(x, y):
    return float(x) * (PITCH_LENGTH_M / 100.0), float(y) * (PITCH_WIDTH_M / 100.0)


def distance_to_goal(x, y) -> float:
    """Metres from (x, y) to the centre of the opponent's goal."""
    mx, my = _to_metres(x, y)
    return math.hypot(PITCH_LENGTH_M - mx, (PITCH_WIDTH_M / 2.0) - my)


# ── Tactical flags ───────────────────────────────────────────────────────────

def is_box_entry(x, y, end_x, end_y, is_corner=False, is_freekick=False) -> bool:
    """Starts outside the attacking box, ends inside it. Open play only."""
    if is_corner or is_freekick:
        return False
    try:
        x, y, end_x, end_y = float(x), float(y), float(end_x), float(end_y)
    except (TypeError, ValueError):
        return False
    return (not in_box(x, y)) and in_box(end_x, end_y)


def is_final_third_entry(x, end_x, is_corner=False, is_freekick=False) -> bool:
    """Starts outside the final third, ends inside it. Open play only."""
    if is_corner or is_freekick:
        return False
    try:
        return float(x) < FINAL_THIRD_X <= float(end_x)
    except (TypeError, ValueError):
        return False


def is_switch_of_play(y, end_y, is_long_ball=False) -> bool:
    """
    A pass from one flank/half-space to the opposite one. Gated on is_long_ball,
    as ClipMaker does, to avoid counting short cross-pitch knocks — which means
    short switches through the lines are missed. That's a deliberate trade for
    precision over recall.
    """
    if not is_long_ball:
        return False
    start, end = pitch_zone(y), pitch_zone(end_y)
    left = {"Left Wing", "Left Half Space"}
    right = {"Right Wing", "Right Half Space"}
    return (start in left and end in right) or (start in right and end in left)


def is_diagonal_long_ball(x, y, end_x, end_y) -> bool:
    """Meaningful forward AND lateral progress, without being a pure switch."""
    try:
        x, y, end_x, end_y = float(x), float(y), float(end_x), float(end_y)
    except (TypeError, ValueError):
        return False
    forward = end_x - x
    lateral = abs(end_y - y)
    if forward < 12 or lateral < 18:
        return False
    ratio = lateral / max(forward, 1e-9)
    return 0.45 <= ratio <= 2.2


def is_deep_completion(end_x, end_y, outcome="", is_cross=False,
                       is_corner=False, is_freekick=False) -> bool:
    """Completed non-cross pass ending within 20m of the opponent's goal centre."""
    if is_cross or is_corner or is_freekick:
        return False
    if str(outcome).lower() == "unsuccessful":
        return False
    try:
        return distance_to_goal(end_x, end_y) <= 20.0
    except (TypeError, ValueError):
        return False


def progressive_distance(x, y, end_x, end_y) -> float:
    """
    Metres of ground gained toward the opponent's goal. Positive = progressive.
    See module docstring for why this differs from ClipMaker's version.
    """
    try:
        return distance_to_goal(x, y) - distance_to_goal(end_x, end_y)
    except (TypeError, ValueError):
        return 0.0


PROGRESSIVE_THRESHOLD_M = 10.0


# ── xT (expected threat) ─────────────────────────────────────────────────────

_XT_GRID = None


def load_xt_grid(path: str = None):
    """
    Loads the xT grid (rows = y bands, cols = x bands). Cached after first read.
    Returns None if the file is missing, so xT simply doesn't get applied rather
    than breaking the scrape.
    """
    global _XT_GRID
    if _XT_GRID is not None:
        return _XT_GRID
    path = path or os.path.join(_CONFIG_DIR, "xT_Grid.csv")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            grid = [[float(v) for v in row] for row in csv.reader(fh) if row]
        _XT_GRID = grid if grid else None
    except (OSError, ValueError):
        _XT_GRID = None
    return _XT_GRID


def xt_value(x, y, grid=None):
    """Threat value of a pitch location, or None if the grid is unavailable."""
    grid = grid or load_xt_grid()
    if not grid:
        return None
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return None
    rows, cols = len(grid), len(grid[0])
    cx = min(cols - 1, max(0, int(x / 100.0 * cols)))
    cy = min(rows - 1, max(0, int(y / 100.0 * rows)))
    return grid[cy][cx]


def xt_delta(x, y, end_x, end_y, grid=None):
    """
    Threat added by moving the ball from start to end. Positive = more dangerous.
    Applies to successful passes and carries only (the caller decides).
    """
    grid = grid or load_xt_grid()
    if not grid:
        return None
    start = xt_value(x, y, grid)
    end = xt_value(end_x, end_y, grid)
    if start is None or end is None:
        return None
    return end - start


# ── Opta reference ───────────────────────────────────────────────────────────

_OPTA = None


def opta_event_types() -> dict:
    """{id: name} for every Opta event type — fuller than any hardcoded list."""
    global _OPTA
    if _OPTA is not None:
        return _OPTA
    try:
        with open(os.path.join(_CONFIG_DIR, "opta_event_reference.json"),
                  encoding="utf-8") as fh:
            _OPTA = json.load(fh).get("event_types", {})
    except (OSError, ValueError):
        _OPTA = {}
    return _OPTA


# ── Enrichment ───────────────────────────────────────────────────────────────

TACTICAL_FLAGS = [
    "is_box_entry",
    "is_final_third_entry",
    "is_switch",
    "is_diagonal_long_ball",
    "is_deep_completion",
    "is_progressive",
]

MOVE_TYPES = {"Pass", "Carry", "TakeOn"}


def enrich_event(ev: dict) -> dict:
    """
    Adds tactical flags, zones, xT and progressive distance to one parsed event
    (mutates and returns it). Safe on events with no coordinates — everything
    degrades to False / "" / 0.0.
    """
    x, y = ev.get("x", 0), ev.get("y", 0)
    end_x, end_y = ev.get("endX", 0), ev.get("endY", 0)
    etype = ev.get("type", "")
    outcome = ev.get("outcome", "")
    is_corner = bool(ev.get("is_corner"))
    is_freekick = bool(ev.get("is_freekick"))
    is_cross = bool(ev.get("is_cross"))
    is_long = bool(ev.get("is_long_ball"))

    # A move event needs a real destination; many events have endX/endY of 0.
    has_end = False
    try:
        has_end = (float(end_x) != 0.0 or float(end_y) != 0.0)
    except (TypeError, ValueError):
        pass
    movey = etype in MOVE_TYPES and has_end

    ev["start_zone"] = pitch_zone(y)
    ev["end_zone"] = pitch_zone(end_y) if has_end else ""
    ev["depth_zone"] = depth_zone(x)
    ev["in_box"] = in_box(x, y)

    ev["is_box_entry"] = movey and is_box_entry(x, y, end_x, end_y, is_corner, is_freekick)
    ev["is_final_third_entry"] = movey and is_final_third_entry(x, end_x, is_corner, is_freekick)
    ev["is_switch"] = movey and etype == "Pass" and is_switch_of_play(y, end_y, is_long)
    ev["is_diagonal_long_ball"] = (movey and etype == "Pass" and is_long
                                   and is_diagonal_long_ball(x, y, end_x, end_y))
    ev["is_deep_completion"] = (movey and etype == "Pass"
                                and is_deep_completion(end_x, end_y, outcome,
                                                       is_cross, is_corner, is_freekick))

    prog = progressive_distance(x, y, end_x, end_y) if movey else 0.0
    ev["prog_distance"] = round(prog, 1)
    ev["is_progressive"] = bool(
        movey and str(outcome).lower() == "successful" and prog >= PROGRESSIVE_THRESHOLD_M
    )

    xt = (xt_delta(x, y, end_x, end_y)
          if movey and str(outcome).lower() == "successful" else None)
    ev["xT"] = round(xt, 4) if xt is not None else None
    return ev


def enrich_events(events: list) -> list:
    """Applies enrich_event across a list, loading the grid once."""
    load_xt_grid()
    for ev in events:
        enrich_event(ev)
    return events
