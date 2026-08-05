"""Placeable redstone cells: the standard-cell library for the calculator.

Everything in the calculator is built from one primitive, the **NOR cell**,
because a redstone torch is naturally a NOR gate: it is lit unless the block it
is attached to is powered, and any number of dust lines may power that block.

Cell geometry (local coordinates, signal flows toward -X)::

    y=1                 [d]                        d = base-top dust
    y=0   [out] [T] [base] [collector...]          T = torch (attached east)
    y=-1  [___]      ....  [_____________]         floor

    x=    -3    -2    -1     0   +1 ...

  * dust on top of the base weakly powers it, and links to the collector by
    the ordinary staircase rule, so the collector never has to rely on dust
    "pointing" at a bare block
  * the torch is attached to the base's west face and drives the output dust
  * output and collector are both at y=0, so cells tile on a single plane

Latency: one redstone tick (2 game ticks) per cell.
"""

from __future__ import annotations

from .engine import World


class Placer:
    """Places blocks in a local frame — an origin — inside a World.

    It used to rotate too: a quarter-turn table, a direction remapper, and a
    `sub()` that composed frames. Nothing ever asked for a rotation. Every
    `Placer` in the repository is built at the default `rot=0`, `sub()` was
    called nowhere at all, and `rscalc/pla.py` — which places the real machine —
    does not use this class. So the whole path was untestable by use, and
    `tools/mutate_core.py` proved it: turning the quarter-turn table the wrong
    way round left the entire suite green. An untested rotation is not a
    feature, it is a mirrored build waiting for the first person who tries it,
    so it is gone rather than pinned.
    """

    def __init__(self, world: World, origin=(0, 0, 0)):
        self.w = world
        self.origin = origin

    def wp(self, p):
        o = self.origin
        return (o[0] + p[0], o[1] + p[1], o[2] + p[2])

    # -- guarded placement: never silently overwrite a different block --
    def _put(self, pos, kind):
        cur = self.w.get(pos)
        if cur is not None and cur.kind != kind:
            raise AssertionError(
                f"block collision at {pos}: {cur.kind} already there, wanted {kind}")
        return pos

    def solid(self, p):
        pos = self.wp(p)
        cur = self.w.get(pos)
        if cur is not None:
            if cur.kind == "solid":
                return pos
            raise AssertionError(f"collision at {pos}: {cur.kind} vs solid")
        self.w.solid(pos)
        return pos

    def wire(self, p):
        pos = self.wp(p)
        self._put(pos, "redstone_wire")
        self.w.wire(pos)
        return pos

    def torch(self, p, attach):
        pos = self.wp(p)
        self._put(pos, "redstone_torch")
        self.w.torch(pos, attach=attach)
        return pos

    def repeater(self, p, facing, delay=1):
        pos = self.wp(p)
        self._put(pos, "repeater")
        self.w.repeater(pos, facing=facing, delay=delay)
        return pos

    def lever(self, p, attach="down", on=False):
        pos = self.wp(p)
        self.w.lever(pos, attach=attach, on=on)
        return pos

    def lamp(self, p):
        pos = self.wp(p)
        self.w.lamp(pos)
        return pos


# --- cells ------------------------------------------------------------------

def nor_cell(pl: Placer, collector_len=1):
    """Place a NOR cell. Returns (collector_positions, output_position).

    `collector_len` extends the input collector along Z so more dust lines can
    merge into one gate; a torch NORs however many inputs reach its base.
    """
    # base + the dust cap that powers it
    pl.solid((-1, 0, 0))
    pl.solid((-1, -1, 0))
    pl.wire((-1, 1, 0))

    # collector run (must stay clear above so the staircase link works)
    cols = []
    half = collector_len // 2
    for i in range(-half, collector_len - half):
        pl.solid((0, -1, i))
        cols.append(pl.wire((0, 0, i)))

    # inverting torch and its output
    pl.torch((-2, 0, 0), attach="east")
    pl.solid((-3, -1, 0))
    out = pl.wire((-3, 0, 0))
    return cols, out
