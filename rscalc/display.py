"""A seven-segment digit built out of redstone lamps.

Laid flat in the X-Z plane so it reads from above, one lamp per cell::

     . a a a .        x ->
     f . . . b
     f . . . b
     f . . . b
     . g g g .
     e . . . c
     e . . . c
     e . . . c
     . d d d .

Each bar is a run of lamps with dust along the top: dust resting on a lamp
weakly powers it, and the run carries along the whole bar, so one feed lights
every lamp in the segment.

The centre bar is boxed in on all four sides by the other segments, so feeding
it from the side is impossible — every segment is therefore fed *from below*
by a two-torch tower, which is non-inverting and strongly powers the lamp it
sits under. The dust cap then picks that up as a full 15 and spreads it.
Feeding all seven the same way keeps the module symmetric.
"""

from __future__ import annotations

from .engine import World

SEGMENTS = {
    "a": [(1, 0), (2, 0), (3, 0)],
    "f": [(0, 1), (0, 2), (0, 3)],
    "b": [(4, 1), (4, 2), (4, 3)],
    "g": [(1, 4), (2, 4), (3, 4)],
    "e": [(0, 5), (0, 6), (0, 7)],
    "c": [(4, 5), (4, 6), (4, 7)],
    "d": [(1, 8), (2, 8), (3, 8)],
}
#: cell each segment is driven through, and which side its feed lane runs off to
FEED = {
    "a": ((2, 0), -1), "f": ((0, 2), -1), "b": ((4, 2), +1),
    "g": ((2, 4), -1), "e": ((0, 6), -1), "c": ((4, 6), +1),
    "d": ((2, 8), -1),
}
DIGIT_SEGMENTS = {
    0: "abcdef", 1: "bc",     2: "abdeg",   3: "abcdg", 4: "bcfg",
    5: "acdfg",  6: "acdefg", 7: "abc",     8: "abcdefg", 9: "abcdfg",
}
SEGS = "abcdefg"

LAMP_Y = 0        # the lamps themselves
DUST_Y = 1        # dust cap that spreads power along a bar
FEED_Y = -4       # plane the seven feeds run in


def build_digit(world: World | None = None, origin=(0, 0, 0), lane=6):
    """Place one digit. Returns (levers, lamps) keyed by segment."""
    w = world or World()
    ox, oy, oz = origin

    def P(x, y, z):
        return (ox + x, oy + y, oz + z)

    lamps = {}
    for seg, cells in SEGMENTS.items():
        lamps[seg] = []
        for (x, z) in cells:
            w.lamp(P(x, LAMP_Y, z))
            w.wire(P(x, DUST_Y, z))          # dust rests on the lamp
            lamps[seg].append(P(x, LAMP_Y, z))

    levers = {}
    for seg, ((fx, fz), side) in FEED.items():
        # two-torch tower: non-inverting, strongly powers the lamp above it
        w.solid(P(fx, FEED_Y, fz))                       # base
        w.torch(P(fx, FEED_Y + 1, fz), attach="down")
        w.solid(P(fx, FEED_Y + 2, fz))
        w.torch(P(fx, FEED_Y + 3, fz), attach="down")    # powers the lamp

        # feed lane out to a lever, running away from the digit
        x = fx
        for _ in range(lane):
            x += side
            w.solid(P(x, FEED_Y - 1, fz))
            w.wire(P(x, FEED_Y, fz))
        x += side
        w.solid(P(x, FEED_Y - 1, fz))
        w.lever(P(x, FEED_Y, fz), attach="down", on=False)
        levers[seg] = P(x, FEED_Y, fz)
    return w, levers, lamps


def lit_segments(engine, lamps):
    """Which segments are actually glowing, read off the lamps."""
    out = ""
    for seg in SEGS:
        if all(engine.read(p) > 0 for p in lamps[seg]):
            out += seg
        elif any(engine.read(p) > 0 for p in lamps[seg]):
            out += seg.upper()      # partially lit: a bug, and visible as one
    return out
