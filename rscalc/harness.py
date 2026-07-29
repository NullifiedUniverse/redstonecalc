"""Wiring loom: carries compiled logic outputs to the display's feed lanes.

The two ends never line up. A compiled netlist puts its outputs wherever the
placer found room, on a four-block Z grid; a seven-segment digit needs its feeds
at seven specific Z values two apart. Something has to cross between them, and
dust that crosses dust merges.

The loom solves that the way a two-layer board does — one direction per level::

    y_disp   the display's own feed lanes (X)
    yb       Z-runs, one X column per net
    ya       X-runs, one Z lane per net
    y_out    the compiled outputs

Within a level every run is parallel, so nothing can touch; between levels the
signal climbs a torch tower, which is non-inverting in pairs. Sources already
sit on distinct Z and each net gets its own turn column, so no two nets ever
share a cell and the loom needs no search — the assignment alone is enough.
"""

from __future__ import annotations

from .engine import World

STEP = 4          # a non-inverting torch pair climbs four levels
TURN_PITCH = 4    # X spacing between turn columns
MAX_RUN = 11      # repeat before dust can fade out


def tower(w: World, x, y, z, height):
    """Non-inverting climb: torches in pairs, four levels each.

    Returns the block on top, which is strongly powered whenever the base is —
    so adjacent dust reads a full 15 off it.
    """
    assert height > 0 and height % STEP == 0, "towers climb in fours"
    w.solid((x, y, z))
    yy = y
    while yy < y + height:
        w.torch((x, yy + 1, z), attach="down")
        w.solid((x, yy + 2, z))
        yy += 2
    return (x, y + height, z)


def line(w: World, a, b, y, fixed, axis, lead=True):
    """A straight dust run along `axis` at height `y`, with repeaters.

    `lead` starts the run on a repeater, which is what picks the signal up off
    a tower's strongly-powered top block and restores it to 15.
    """
    if (b - a) == 0 and not lead:
        return
    step = 1 if b >= a else -1
    facing = ({"x": "east", "z": "south"} if step > 0
              else {"x": "west", "z": "north"})[axis]
    since = MAX_RUN if lead else 0
    for c in range(a, b + step, step):
        p = (c, y, fixed) if axis == "x" else (fixed, y, c)
        w.solid((p[0], y - 1, p[2]))
        if since >= MAX_RUN:
            w.repeater(p, facing=facing, delay=1)
            since = 0
        else:
            w.wire(p)
            since += 1


def route(w: World, nets, ya, yb, y_disp, turn_x0):
    """Route (source dust, target dust) pairs from compiled logic to a display.

    Pass `nets` ordered by source Z; that order picks the turn columns, and
    keeping it monotonic keeps the Z-runs from doubling back across each other.
    """
    stats = {"nets": 0, "towers": 0}
    for i, (src, dst) in enumerate(nets):
        sx, sy, sz = src
        dx, _dy, dz = dst
        turn = turn_x0 + TURN_PITCH * i

        # step clear of the output, then climb to the X level
        for x in (sx + 1, sx + 2):
            w.solid((x, sy - 1, sz))
            w.wire((x, sy, sz))
        tower(w, sx + 3, sy, sz, ya - sy)
        line(w, sx + 4, turn - 1, ya, sz, "x")

        # climb to the Z level and cross to the target's Z
        tower(w, turn, ya, sz, yb - ya)
        zstep = 1 if dz >= sz else -1
        line(w, sz + zstep, dz - zstep, yb, turn, "z")

        # climb to the display and run in to its lane
        tower(w, turn, yb, dz, y_disp - yb)
        line(w, turn + 1, dx, y_disp, dz, "x")
        stats["nets"] += 1
        stats["towers"] += 3
    return stats
