"""A second redstone simulator, written from the rules rather than from the first.

`rscalc/engine.py` is fast: it precomputes dust nets, link tables, strong-power
maps and reverse dependency sets, then propagates incrementally through dirty
sets. Every one of those is an opportunity to be subtly wrong in a way its own
tests cannot see, because they were written against the same understanding.

So this is a deliberately stupid second implementation. No precomputation, no
nets, no dirty sets: every tick it recomputes every dust cell from scratch by
flooding outward from every source, and asks every component what it wants to be
by looking directly at its neighbours. It is far too slow for a real build and
that is fine — its only job is to disagree with the fast one if either is wrong.

The rules it implements, stated so they can be checked against the game one at a
time (see `tests/test_minecraft_rules.py`, which tests each one in isolation):

  R1  Signal strength runs 0-15. Dust taken N blocks along a dust path from a
      source of strength S carries S-N, floored at 0.
  R2  Sources at full strength: a lever that is on, a block of redstone, a lit
      redstone torch, the front of a powered repeater. A comparator's front
      carries its computed output instead.
  R3  Strong power. A torch strongly powers the block above it; a repeater or
      comparator strongly powers the block in front of it; a lever strongly
      powers the block it is attached to. Dust beside a strongly powered block
      reads 15.
  R4  Weak power. Dust weakly powers the block beneath it and any block it
      points at. A weakly powered block does *not* power dust beside it.
  R5  Dust connects horizontally to: other dust; a repeater or comparator, but
      only along that component's own axis; a torch, lever or block of redstone.
      It also connects up onto dust sitting on top of an adjacent full block,
      unless a full block sits directly above the dust itself, and down onto
      dust one level below an adjacent non-full cell.
  R6  Pointing follows connection. No connections is a dot: it points nowhere
      and powers only the block beneath it. Exactly one connection makes a
      straight line, which points both that way and the opposite way. Two or
      more, and it points along each connection.
  R7  A torch is off while the block it is attached to is powered, on otherwise,
      after a delay of 2 game ticks.
  R8  Burnout: a torch forced off more than 8 times inside 60 game ticks stops
      working and stays off.
  R9  A repeater reads the block behind it and reproduces it at full strength
      after 2, 4, 6 or 8 game ticks. It is a diode: nothing flows backwards.
  R10 A repeater is locked while a repeater or comparator facing its side is
      powered, and a locked repeater's output does not change at all.
  R11 A comparator compares its rear input against the strongest of its two
      side inputs, after 2 game ticks. In comparison mode it outputs the rear
      strength when the rear is at least the side, otherwise 0; in subtraction
      mode it outputs rear minus side, floored at 0.
  R12 A redstone lamp lights from power of either kind on any of its six sides.
  R13 A component has at most one pending update. When its input changes it
      schedules one, and if a further change arrives before that update fires,
      no second update is scheduled — the pending one simply re-reads the input
      when it fires. So a pulse shorter than a repeater's delay is swallowed
      entirely, which is the behaviour every redstone builder relies on.
"""

from __future__ import annotations

import heapq

from rscalc.engine import (World, DIRS, OPPOSITE, HORIZONTAL, ALL6, AXIS,
                           add, step, CONDUCTIVE)

FULL_CUBE = CONDUCTIVE            # solid and lamp are the full opaque cubes
TORCH_DELAY = 2
COMPARATOR_DELAY = 2
BURNOUT_WINDOW = 60
BURNOUT_LIMIT = 8


class RefEngine:
    """Recomputes everything, every tick. Correct by being obvious."""

    def __init__(self, world: World):
        self.w = world
        self.now = 0
        self.power = {}        # dust cell -> 0..15
        self.lit = {}          # torch -> bool
        self.rep = {}          # repeater -> bool (output on)
        self.cmp = {}          # comparator -> 0..15
        self.lamp = {}         # lamp -> bool
        self.lever = {}
        self.locked = {}
        self.burned = set()
        self._toggles = {}
        self._queue = []       # (time, seq, pos) — the value is read on firing
        self._scheduled = set()
        self._seq = 0
        for pos, b in world.blocks.items():
            if b.kind == "redstone_wire":
                self.power[pos] = 0
            elif b.kind == "redstone_torch":
                self.lit[pos] = True
                self._toggles[pos] = []
            elif b.kind == "repeater":
                self.rep[pos] = False
                self.locked[pos] = False
            elif b.kind == "comparator":
                self.cmp[pos] = 0
            elif b.kind == "lamp":
                self.lamp[pos] = False
            elif b.kind == "lever":
                self.lever[pos] = bool(b.on)

    # ---- R5: what a dust cell connects to -------------------------------
    def connections(self, pos):
        out = {}
        for d in HORIZONTAL:
            n = step(pos, d)
            k = self.w.kind(n)
            if k == "redstone_wire":
                out[d] = n
            elif k in ("repeater", "comparator"):
                if AXIS[self.w.blocks[n].facing] == AXIS[d]:
                    out[d] = n
            elif k in ("redstone_torch", "lever", "redstone_block"):
                out[d] = n
            elif k in FULL_CUBE:
                # up onto dust on top of the neighbour, unless capped
                above_self = add(pos, DIRS["up"])
                if (self.w.kind(add(n, DIRS["up"])) == "redstone_wire"
                        and self.w.kind(above_self) not in FULL_CUBE):
                    out[d] = add(n, DIRS["up"])
            else:
                if self.w.kind(add(n, DIRS["down"])) == "redstone_wire":
                    out[d] = add(n, DIRS["down"])
        return out

    # ---- R6: which way it points ----------------------------------------
    def points(self, pos, d):
        conns = self.connections(pos)
        if not conns:
            return False
        if len(conns) == 1:
            only = next(iter(conns))
            return d in (only, OPPOSITE[only])
        return d in conns

    # ---- R2, R3: what a source hands to dust beside it -------------------
    def source_level(self, pos):
        """Strength this non-dust block offers to dust directly beside it."""
        k = self.w.kind(pos)
        if k == "lever":
            return 15 if self.lever[pos] else 0
        if k == "redstone_block":
            return 15
        if k == "redstone_torch":
            return 15 if self.lit[pos] else 0
        if k == "repeater":
            return 15 if self.rep[pos] else 0
        if k == "comparator":
            return self.cmp[pos]
        return 0

    def strong_level(self, pos):
        """R3: how strongly this block is being powered, 0 or 15 (or a
        comparator's level, which it passes into the block in front)."""
        best = 0
        for d in ALL6:
            n = step(pos, d)
            k = self.w.kind(n)
            if k == "redstone_torch":
                # a torch powers the block above it
                if add(n, DIRS["up"]) == pos and self.lit[n]:
                    best = 15
            elif k in ("repeater", "comparator"):
                if step(n, self.w.blocks[n].facing) == pos:
                    best = max(best, self.source_level(n))
            elif k == "lever":
                if step(n, self.w.blocks[n].attach) == pos and self.lever[n]:
                    best = 15
        return best

    def weak_level(self, pos):
        """R4: dust above powers it; dust pointing at it powers it."""
        best = 0
        up = add(pos, DIRS["up"])
        if self.w.kind(up) == "redstone_wire":
            best = max(best, self.power[up])
        for d in HORIZONTAL:
            n = step(pos, d)
            if self.w.kind(n) != "redstone_wire":
                continue
            if self.power[n] > 0 and self.points(n, OPPOSITE[d]):
                best = max(best, self.power[n])
        return best

    def block_power(self, pos):
        return max(self.strong_level(pos), self.weak_level(pos))

    # ---- R1: flood the whole dust graph from scratch ---------------------
    def recompute_dust(self):
        best = {p: 0 for p in self.power}
        heap = []
        for p in self.power:
            start = 0
            for d in HORIZONTAL + ("down",):
                n = step(p, d)
                k = self.w.kind(n)
                if k in ("lever", "redstone_block", "redstone_torch"):
                    start = max(start, self.source_level(n))
                elif k in ("repeater", "comparator"):
                    # only out of the front
                    if step(n, self.w.blocks[n].facing) == p:
                        start = max(start, self.source_level(n))
                elif k in FULL_CUBE:
                    # dust beside or on top of a strongly powered block reads 15
                    start = max(start, self.strong_level(n))
            if start:
                best[p] = start
                heapq.heappush(heap, (-start, p))
        seen = set()
        while heap:
            neg, p = heapq.heappop(heap)
            v = -neg
            if p in seen or v < best[p]:
                continue
            seen.add(p)
            if v <= 1:
                continue
            for nxt in self.connections(p).values():
                if self.w.kind(nxt) != "redstone_wire":
                    continue
                if v - 1 > best[nxt]:
                    best[nxt] = v - 1
                    heapq.heappush(heap, (-(v - 1), nxt))
        self.power = best

    # ---- what each component wants to be --------------------------------
    def desired(self, pos):
        b = self.w.blocks[pos]
        if b.kind == "redstone_torch":
            return self.block_power(step(pos, b.attach)) == 0      # R7
        if b.kind == "repeater":
            behind = step(pos, OPPOSITE[b.facing])                 # R9
            return self.input_level(behind, pos) > 0
        if b.kind == "comparator":                                 # R11
            rear = self.input_level(step(pos, OPPOSITE[b.facing]), pos)
            side = 0
            for d in HORIZONTAL:
                if AXIS[d] == AXIS[b.facing]:
                    continue
                side = max(side, self.side_level(pos, d))
            if b.mode == "subtract":
                return max(rear - side, 0)
            return rear if rear >= side else 0
        if b.kind == "lamp":                                       # R12
            for d in ALL6:
                n = step(pos, d)
                k = self.w.kind(n)
                if k == "redstone_wire":
                    if self.power[n] > 0 and (d == "down"
                                              or self.points(n, OPPOSITE[d])):
                        return True
                elif k in ("lever", "redstone_block", "redstone_torch"):
                    if self.source_level(n) > 0:
                        return True
                elif k in ("repeater", "comparator"):
                    if (step(n, self.w.blocks[n].facing) == pos
                            and self.source_level(n) > 0):
                        return True
                elif k in FULL_CUBE:
                    if self.block_power(n) > 0:
                        return True
            return False
        return None

    def input_level(self, pos, into):
        """What a repeater or comparator reads out of the cell behind it."""
        k = self.w.kind(pos)
        if k == "redstone_wire":
            return self.power[pos]
        if k in ("lever", "redstone_block", "redstone_torch"):
            return self.source_level(pos)
        if k in ("repeater", "comparator"):
            return (self.source_level(pos)
                    if step(pos, self.w.blocks[pos].facing) == into else 0)
        if k in FULL_CUBE:
            return self.block_power(pos)
        return 0

    def side_level(self, pos, d):
        """A comparator's or repeater's side input: only dust and the front of
        another repeater or comparator count."""
        n = step(pos, d)
        k = self.w.kind(n)
        if k == "redstone_wire":
            return self.power[n]
        if k == "redstone_block":
            return 15
        if k == "redstone_torch":
            return self.source_level(n)
        if k in ("repeater", "comparator"):
            return (self.source_level(n)
                    if step(n, self.w.blocks[n].facing) == pos else 0)
        return 0

    def current(self, pos):
        b = self.w.blocks[pos]
        if b.kind == "redstone_torch":
            return self.lit[pos]
        if b.kind == "repeater":
            return self.rep[pos]
        if b.kind == "comparator":
            return self.cmp[pos]
        if b.kind == "lamp":
            return self.lamp[pos]
        return None

    def apply(self, pos, value):
        b = self.w.blocks[pos]
        if b.kind == "redstone_torch":
            self.lit[pos] = value
        elif b.kind == "repeater":
            self.rep[pos] = value
        elif b.kind == "comparator":
            self.cmp[pos] = value
        elif b.kind == "lamp":
            self.lamp[pos] = value

    def delay_of(self, pos):
        b = self.w.blocks[pos]
        if b.kind == "redstone_torch":
            return TORCH_DELAY
        if b.kind == "repeater":
            return 2 * b.delay
        if b.kind == "comparator":
            return COMPARATOR_DELAY
        return 0

    # ---- R10 ------------------------------------------------------------
    def is_locked(self, pos):
        b = self.w.blocks[pos]
        for d in HORIZONTAL:
            if AXIS[d] == AXIS[b.facing]:
                continue
            n = step(pos, d)
            k = self.w.kind(n)
            if k in ("repeater", "comparator"):
                if (step(n, self.w.blocks[n].facing) == pos
                        and self.source_level(n) > 0):
                    return True
        return False

    # ---- the tick -------------------------------------------------------
    def settle(self):
        """Dust is instantaneous; recompute it and any zero-delay component."""
        for _ in range(200):
            before = dict(self.power)
            self.recompute_dust()
            changed = before != self.power
            for pos in list(self.lamp):
                want = self.desired(pos)
                if self.lamp[pos] != want:
                    self.lamp[pos] = want
                    changed = True
            if not changed:
                return
        raise RuntimeError("dust did not settle")

    def schedule_all(self):
        """R13: one pending update per component, and never a second one."""
        for pos in list(self.lit) + list(self.rep) + list(self.cmp):
            if pos in self._scheduled:
                continue                       # a tick is already coming
            b = self.w.blocks[pos]
            if b.kind == "repeater":
                self.locked[pos] = self.is_locked(pos)
                if self.locked[pos]:
                    continue
            if b.kind == "redstone_torch" and pos in self.burned:
                continue
            if self.desired(pos) == self.current(pos):
                continue
            self._seq += 1
            heapq.heappush(self._queue,
                           (self.now + self.delay_of(pos), self._seq, pos))
            self._scheduled.add(pos)

    def tick(self):
        self.now += 1
        fired = 0
        due = []
        while self._queue and self._queue[0][0] <= self.now:
            due.append(heapq.heappop(self._queue))
        for _, _, pos in due:
            self._scheduled.discard(pos)
            # R13: read the input *now*, not when the update was scheduled — a
            # pulse that came and went in the meantime leaves nothing behind
            b = self.w.blocks[pos]
            if b.kind == "repeater" and self.is_locked(pos):
                continue
            if b.kind == "redstone_torch" and pos in self.burned:
                continue
            value = self.desired(pos)
            if self.current(pos) == value:
                continue
            if b.kind == "redstone_torch":
                hist = [t for t in self._toggles[pos]
                        if t > self.now - BURNOUT_WINDOW]
                hist.append(self.now)
                self._toggles[pos] = hist
                if len(hist) > BURNOUT_LIMIT:
                    self.burned.add(pos)
                    self.lit[pos] = False
                    fired += 1
                    continue
            self.apply(pos, value)
            fired += 1
        self.settle()
        self.schedule_all()
        return fired

    def run_until_stable(self, limit=20000):
        start = self.now
        n = 0
        while self._queue and n < limit:
            self.tick()
            n += 1
        return self.now - start

    def run(self, ticks):
        for _ in range(ticks):
            self.tick()

    def initialize_steady(self, max_iters=500):
        """Relax to a resting state, as the fast engine does, then forget the
        transient so timing starts from zero."""
        for _ in range(max_iters):
            self.settle()
            changed = False
            for pos in list(self.lit) + list(self.rep) + list(self.cmp):
                b = self.w.blocks[pos]
                if b.kind == "repeater":
                    self.locked[pos] = self.is_locked(pos)
                    if self.locked[pos]:
                        continue
                want = self.desired(pos)
                if self.current(pos) != want:
                    self.apply(pos, want)
                    changed = True
            if not changed:
                break
        else:
            raise RuntimeError("no steady state")
        self._queue.clear()
        self._scheduled.clear()
        self.burned.clear()
        for p in self._toggles:
            self._toggles[p] = []
        self.now = 0

    def set_lever(self, pos, on):
        if self.lever[pos] == bool(on):
            return
        self.lever[pos] = bool(on)
        self.settle()
        self.schedule_all()

    # ---- readout --------------------------------------------------------
    def snapshot(self):
        """Everything observable, for comparing against the other engine."""
        s = {}
        for p, v in self.power.items():
            s[p] = ("dust", v)
        for p, v in self.lit.items():
            s[p] = ("torch", bool(v))
        for p, v in self.rep.items():
            s[p] = ("rep", bool(v))
        for p, v in self.cmp.items():
            s[p] = ("cmp", v)
        for p, v in self.lamp.items():
            s[p] = ("lamp", bool(v))
        for p, v in self.lever.items():
            s[p] = ("lever", bool(v))
        return s


def fast_snapshot(engine):
    """The same observables, pulled out of rscalc.engine's representation."""
    s = {}
    for pos, b in engine.w.blocks.items():
        if b.kind == "redstone_wire":
            s[pos] = ("dust", b.power)
        elif b.kind == "redstone_torch":
            s[pos] = ("torch", bool(b.lit))
        elif b.kind == "repeater":
            s[pos] = ("rep", bool(b.powered))
        elif b.kind == "comparator":
            s[pos] = ("cmp", b.out)
        elif b.kind == "lamp":
            s[pos] = ("lamp", b.power > 0)
        elif b.kind == "lever":
            s[pos] = ("lever", bool(b.on))
    return s
