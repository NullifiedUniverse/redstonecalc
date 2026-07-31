"""A seven-segment digit built out of redstone lamps.

Laid flat in the X-Z plane so it reads from above, one lamp per cell::

     . a a a .        x ->
     f . . . b
     f . . . b        z
     f . . . b        |
     f . . . b        v
     f . . . b
     . g g g .
     e . . . c
     e . . . c
     e . . . c
     e . . . c
     e . . . c
     . d d d .

Each bar is a run of lamps with dust along the top: dust resting on a lamp
weakly powers it, and the run carries along the whole bar, so one feed lights
every lamp in the segment.

The centre bar is boxed in on all four sides by the other segments, so feeding
it from the side is impossible. Every segment is therefore fed *from below* by
a torch tower, which is non-inverting in pairs and strongly powers the lamp it
sits under; the dust cap picks that up as a full 15 and spreads it.

The bars are five lamps tall rather than three for a routing reason, not a
visual one. Feeds arrive as dust lanes in the plane below, and two lanes one
block apart would merge, so the seven towers need seven Z values at least two
apart. Three-tall bars cannot do that — the two vertical bars share too narrow
a band — but five-tall bars give exactly ``0 2 4 6 8 10 12``, so all seven
feeds can arrive from the same side without a single crossing.
"""

from __future__ import annotations

from .engine import World

SEGMENTS = {
    "a": [(1, 0), (2, 0), (3, 0)],
    "f": [(0, z) for z in range(1, 6)],
    "b": [(4, z) for z in range(1, 6)],
    "g": [(1, 6), (2, 6), (3, 6)],
    "e": [(0, z) for z in range(7, 12)],
    "c": [(4, z) for z in range(7, 12)],
    "d": [(1, 12), (2, 12), (3, 12)],
}
#: cell each segment is driven through — chosen so the seven Z values are
#: 0,2,4,6,8,10,12 and every feed lane can come in from -X without crossing
FEED = {
    "a": (2, 0), "f": (0, 2), "b": (4, 4), "g": (2, 6),
    "e": (0, 8), "c": (4, 10), "d": (2, 12),
}
DIGIT_SEGMENTS = {
    0: "abcdef", 1: "bc",     2: "abdeg",   3: "abcdg", 4: "bcfg",
    5: "acdfg",  6: "acdefg", 7: "abc",     8: "abcdefg", 9: "abcdfg",
}
SEGS = "abcdefg"

LAMP_Y = 0        # the lamps themselves
DUST_Y = 1        # dust cap that spreads power along a bar


def seven_seg(nl, d, enable=None):
    """A BCD digit (4 nodes, LSB first) -> the seven segment lines.

    The ten minterms are built once and shared by all seven collectors, so the
    whole decoder is 17 gates and exactly two stages. Inputs 10-15 never occur
    and drive nothing, which blanks the digit rather than showing nonsense.

    `enable` is an extra literal folded into every minterm rather than ANDed
    onto the outputs, so leading-zero blanking is free — a term is already an
    AND, and one more literal costs nothing.

    Digits whose upper bits are the constant rail (the top digit of a value
    that cannot reach 2000, say) fold: impossible values drop out of the sum,
    and a segment lit for every value the digit *can* take becomes a constant.
    """
    from .logic import fold_term, nand_term
    nmin = {}
    for k in range(10):
        term = [(d[i], bool((k >> i) & 1)) for i in range(4)]
        if enable is not None:
            term.append((enable, True))
        nmin[k] = fold_term(nl, term)

    out = {}
    for s in SEGS:
        ks = [k for k in range(10) if s in DIGIT_SEGMENTS[k]]
        if any(nmin[k] == 1 for k in ks):
            out[s] = nl.one()                     # lit for every reachable value
            continue
        live = [nand_term(nl, nmin[k]) for k in ks if nmin[k] != 0]
        out[s] = nl.gate([(t, True) for t in live]) if live else nl.zero()
    return out


def build_digit(world: World | None = None, origin=(0, 0, 0), feed_drop=4,
                lane_len=6):
    """Place one digit.

    `feed_drop` is how far below the lamps the feed lanes run; it must be even,
    since each torch pair in the tower is what makes the riser non-inverting.
    Returns (world, feed_entry, lamps) where `feed_entry` gives, per segment,
    the far end of its lane — the point a harness has to deliver to.
    """
    assert feed_drop % 4 == 0, "each non-inverting torch pair spans four levels"
    w = world or World()
    ox, oy, oz = origin
    feed_y = -feed_drop

    def P(x, y, z):
        return (ox + x, oy + y, oz + z)

    lamps = {}
    for seg, cells in SEGMENTS.items():
        lamps[seg] = []
        for (x, z) in cells:
            w.lamp(P(x, LAMP_Y, z))
            w.wire(P(x, DUST_Y, z))          # dust rests on the lamp
            lamps[seg].append(P(x, LAMP_Y, z))

    entries = {}
    for seg, (fx, fz) in FEED.items():
        # torch tower: each pair of torches is non-inverting and climbs 4
        y = feed_y
        w.solid(P(fx, y, fz))                            # base
        while y < LAMP_Y - 1:
            w.torch(P(fx, y + 1, fz), attach="down")
            if y + 2 < LAMP_Y:
                w.solid(P(fx, y + 2, fz))
            y += 2
        # the topmost torch strongly powers the lamp itself, so the tower needs
        # no block of its own up there

        # feed lane running out to -X
        for i in range(1, lane_len + 1):
            w.solid(P(fx - i, feed_y - 1, fz))
            w.wire(P(fx - i, feed_y, fz))
        entries[seg] = P(fx - lane_len, feed_y, fz)
    return w, entries, lamps


def build_lamp_row(world: World, origin=(0, 0, 0), n=4, feed_drop=4,
                   lane_len=6, pitch=2):
    """A row of single lamps fed from below, for status flags.

    Same construction as a digit's segment: a torch tower strongly powers the
    lamp from underneath and a feed lane runs out to -X. `pitch` is the Z
    spacing, which must be at least 2 so the feed lanes cannot merge.
    """
    assert pitch >= 2, "feed lanes one block apart would merge"
    assert feed_drop % 4 == 0, "each non-inverting torch pair spans four levels"
    ox, oy, oz = origin
    feed_y = oy - feed_drop
    entries, lamps = [], []
    for i in range(n):
        z = oz + pitch * i
        world.lamp((ox, oy, z))
        lamps.append((ox, oy, z))
        y = feed_y
        world.solid((ox, y, z))
        while y < oy - 1:
            world.torch((ox, y + 1, z), attach="down")
            if y + 2 < oy:
                world.solid((ox, y + 2, z))
            y += 2
        for k in range(1, lane_len + 1):
            world.solid((ox - k, feed_y - 1, z))
            world.wire((ox - k, feed_y, z))
        entries.append((ox - lane_len, feed_y, z))
    return entries, lamps


def lit_segments(engine, lamps):
    """Which segments are actually glowing, read off the lamps."""
    out = ""
    for seg in SEGS:
        vals = [engine.read(p) > 0 for p in lamps[seg]]
        if all(vals):
            out += seg
        elif any(vals):
            out += seg.upper()      # partially lit: a bug, and visible as one
    return out


def read_digit(engine, lamps):
    """The numeral the lamps are showing, or None if it is not a numeral."""
    shown = lit_segments(engine, lamps)
    for d, pat in DIGIT_SEGMENTS.items():
        if shown == pat:
            return d
    return None
