"""Tick-accurate redstone simulator for Minecraft Java Edition (26.2 semantics).

Implements the subset of redstone needed for combinational/sequential logic:

  * signal strength 0-15, decrementing one per dust block
  * strong vs weak power (weakly powered blocks do NOT power adjacent dust)
  * redstone torch inversion with a 2 game-tick delay, plus burnout
  * repeaters: 2/4/6/8 game-tick diodes, signal restoration, side locking
  * comparators: compare / subtract modes, 2 game-tick delay
  * levers, blocks of redstone, redstone lamps

Timing is in *game ticks* (1 gt = 1/20 s). A "redstone tick" is 2 gt.

Model notes / deliberate simplifications (documented so results are honest):
  * Dust power propagation is instantaneous within a tick, as in the game.
  * Update *order* within a single tick is resolved by settling to a fixed
    point rather than reproducing Java's neighbour-update traversal order.
    Order-dependent quirks (0-tick pulses, locational randomness) are
    therefore not reproduced. Well-formed synchronous logic is unaffected;
    `World.lint()` flags constructs that would depend on those quirks.
  * Quasi-connectivity is not modelled; no pistons are used in this project.
"""

from __future__ import annotations

import heapq
from collections import defaultdict

# --- geometry ---------------------------------------------------------------

DIRS = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
    "up": (0, 1, 0),
    "down": (0, -1, 0),
}
OPPOSITE = {
    "north": "south", "south": "north",
    "west": "east", "east": "west",
    "up": "down", "down": "up",
}
HORIZONTAL = ("north", "south", "east", "west")
ALL6 = ("north", "south", "east", "west", "up", "down")
AXIS = {"north": "z", "south": "z", "east": "x", "west": "x", "up": "y", "down": "y"}

UP = (0, 1, 0)
DOWN = (0, -1, 0)


def add(p, d):
    return (p[0] + d[0], p[1] + d[1], p[2] + d[2])


def step(p, direction):
    return add(p, DIRS[direction])


# --- blocks -----------------------------------------------------------------

#: kinds that are full opaque cubes: they conduct redstone power and support dust
CONDUCTIVE = {"solid", "lamp"}
#: kinds that dust can be placed on top of
SUPPORTS_DUST = CONDUCTIVE | {"redstone_block", "glass"}


class Block:
    """A single voxel. Mutable fields carry simulation state."""

    __slots__ = ("kind", "facing", "attach", "delay", "mode",
                 "lit", "powered", "out", "on", "power", "locked",
                 "_toggles", "label")

    def __init__(self, kind, facing="north", attach="down", delay=1,
                 mode="compare", on=False, label=None):
        self.kind = kind
        self.facing = facing      # repeater/comparator: direction of the FRONT (output)
        self.attach = attach      # torch/lever: direction from this block to its support
        self.delay = delay        # repeater: 1..4 redstone ticks
        self.mode = mode          # comparator: "compare" | "subtract"
        self.label = label

        self.lit = True           # torch
        self.powered = False      # repeater output
        self.out = 0              # comparator output
        self.on = on              # lever
        self.power = 0            # dust signal strength / lamp lit
        self.locked = False
        self._toggles = []        # torch burnout bookkeeping

    def copy(self):
        b = Block(self.kind, self.facing, self.attach, self.delay, self.mode,
                  self.on, self.label)
        return b

    def __repr__(self):
        return f"<{self.kind}>"


# --- world ------------------------------------------------------------------

class World:
    """A sparse voxel volume of redstone components."""

    def __init__(self):
        self.blocks: dict[tuple[int, int, int], Block] = {}
        self.ports: dict[str, tuple[int, int, int]] = {}

    # -- placement --
    def set(self, pos, block: Block):
        self.blocks[pos] = block
        return block

    def get(self, pos):
        return self.blocks.get(pos)

    def kind(self, pos):
        b = self.blocks.get(pos)
        return b.kind if b else "air"

    def solid(self, pos, kind="solid"):
        return self.set(pos, Block(kind))

    def wire(self, pos):
        return self.set(pos, Block("redstone_wire"))

    def torch(self, pos, attach="down"):
        return self.set(pos, Block("redstone_torch", attach=attach))

    def repeater(self, pos, facing, delay=1):
        return self.set(pos, Block("repeater", facing=facing, delay=delay))

    def comparator(self, pos, facing, mode="compare"):
        return self.set(pos, Block("comparator", facing=facing, mode=mode))

    def lever(self, pos, attach="down", on=False):
        return self.set(pos, Block("lever", attach=attach, on=on))

    def lamp(self, pos):
        return self.set(pos, Block("lamp"))

    def redstone_block(self, pos):
        return self.set(pos, Block("redstone_block"))

    def port(self, name, pos):
        self.ports[name] = pos

    # -- queries --
    def is_conductive(self, pos):
        return self.kind(pos) in CONDUCTIVE

    def bounds(self):
        if not self.blocks:
            return (0, 0, 0), (0, 0, 0)
        xs = [p[0] for p in self.blocks]
        ys = [p[1] for p in self.blocks]
        zs = [p[2] for p in self.blocks]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def count(self, kind=None):
        if kind is None:
            return len(self.blocks)
        return sum(1 for b in self.blocks.values() if b.kind == kind)

    # -- dust topology (static: depends only on geometry) --
    def dust_connections(self, pos):
        """Horizontal directions in which dust at `pos` links to something."""
        out = {}
        for d in HORIZONTAL:
            n = step(pos, d)
            k = self.kind(n)
            if k == "redstone_wire":
                out[d] = n
            elif k in ("repeater", "comparator"):
                b = self.blocks[n]
                if AXIS[b.facing] == AXIS[d]:
                    out[d] = n
            elif k in ("redstone_torch", "lever", "redstone_block"):
                out[d] = n
            elif k in CONDUCTIVE:
                # staircase up: dust on top of the neighbouring block, provided
                # the block directly above us does not cut the connection off
                if (self.kind(add(n, UP)) == "redstone_wire"
                        and not self.is_conductive(add(pos, UP))):
                    out[d] = add(n, UP)
            else:
                # staircase down: dust one level below in that direction
                if self.kind(add(n, DOWN)) == "redstone_wire":
                    out[d] = add(n, DOWN)
        return out

    def wire_points(self, pos, d):
        """Does dust at `pos` point (and therefore weakly power) toward `d`?"""
        conns = self.dust_connections(pos)
        if not conns:
            return False           # isolated dot: powers only the block beneath
        if len(conns) == 1:
            only = next(iter(conns))
            return d == only or d == OPPOSITE[only]
        return d in conns

    # -- design rule check --
    def lint(self):
        """Flag constructions whose behaviour this model does not guarantee."""
        problems = []
        for pos, b in self.blocks.items():
            if b.kind == "redstone_torch":
                support = step(pos, b.attach)
                if not self.is_conductive(support):
                    problems.append(f"torch at {pos} has no conductive support at {support}")
                if b.attach == "up":
                    problems.append(f"torch at {pos} attached to a block bottom (illegal)")
                # a torch horizontally beside a solid block that carries another
                # torch would depend on weak-power-from-torch, which we do not model
                for d in HORIZONTAL:
                    n = step(pos, d)
                    if n == support or not self.is_conductive(n):
                        continue
                    for d2 in ALL6:
                        o = step(n, d2)
                        ob = self.get(o)
                        if (ob and ob.kind == "redstone_torch"
                                and step(o, ob.attach) == n):
                            problems.append(
                                f"torch at {pos} is beside block {n} which carries torch {o}")
            elif b.kind == "redstone_wire":
                below = add(pos, DOWN)
                if self.kind(below) not in SUPPORTS_DUST:
                    problems.append(f"dust at {pos} unsupported (below is {self.kind(below)})")
            elif b.kind in ("repeater", "comparator"):
                below = add(pos, DOWN)
                if self.kind(below) not in SUPPORTS_DUST:
                    problems.append(f"{b.kind} at {pos} unsupported")
        return problems


# --- simulator --------------------------------------------------------------

COMPONENT_KINDS = {"redstone_torch", "repeater", "comparator", "lamp"}
#: kinds that can act as a redstone power source in their own right
SOURCE_KINDS = {"redstone_torch", "repeater", "comparator", "lever", "redstone_block"}

TORCH_DELAY = 2       # game ticks
COMPARATOR_DELAY = 2  # game ticks
BURNOUT_WINDOW = 60   # game ticks
BURNOUT_LIMIT = 8     # toggles within the window


class Engine:
    """Event-driven redstone simulator over a `World`."""

    def __init__(self, world: World, model_burnout=True):
        self.w = world
        self.now = 0
        self.model_burnout = model_burnout
        self._queue = []          # heap of (tick, seq, pos, target)
        self._seq = 0
        self._pending = {}        # pos -> target state already scheduled
        self.burned_out = set()
        self._compile()
        self.settle()

    # ---- static topology ----
    def _compile(self):
        w = self.w
        self.dust_links = {}
        for pos, b in w.blocks.items():
            if b.kind == "redstone_wire":
                self.dust_links[pos] = [
                    p for p in w.dust_connections(pos).values()
                    if w.kind(p) == "redstone_wire"
                ]
        # cached "does this dust point that way" table
        self.points = {
            pos: {d: w.wire_points(pos, d) for d in HORIZONTAL}
            for pos in self.dust_links
        }

        # connected components of dust
        self.net_of = {}
        self.nets = []
        for pos in self.dust_links:
            if pos in self.net_of:
                continue
            nid = len(self.nets)
            stack, members = [pos], []
            self.net_of[pos] = nid
            while stack:
                cur = stack.pop()
                members.append(cur)
                for nxt in self.dust_links[cur]:
                    if nxt not in self.net_of:
                        self.net_of[nxt] = nid
                        stack.append(nxt)
            self.nets.append(members)

        self.components = [p for p, b in w.blocks.items() if b.kind in COMPONENT_KINDS]

        # which component positions strongly power a given solid block
        self._strong_srcs = defaultdict(list)
        # which nets weakly power a given solid block
        self._weak_srcs = defaultdict(list)
        for pos, b in w.blocks.items():
            if b.kind == "redstone_torch":
                self._strong_srcs[add(pos, UP)].append(pos)
            elif b.kind in ("repeater", "comparator"):
                self._strong_srcs[step(pos, b.facing)].append(pos)
            elif b.kind == "lever":
                self._strong_srcs[step(pos, b.attach)].append(pos)
            elif b.kind == "redstone_wire":
                nid = self.net_of[pos]
                self._weak_srcs[add(pos, DOWN)].append(nid)
                for d in HORIZONTAL:
                    if self.points[pos][d]:
                        self._weak_srcs[step(pos, d)].append(nid)

        # net -> source components; component -> nets it drives
        self.net_sources = defaultdict(list)   # nid -> [(dust_pos, dir)]
        self.nets_driven_by = defaultdict(set)
        for nid, members in enumerate(self.nets):
            for dpos in members:
                for d in ALL6:
                    n = step(dpos, d)
                    k = w.kind(n)
                    if k in SOURCE_KINDS:
                        self.net_sources[nid].append((dpos, d))
                        self.nets_driven_by[n].add(nid)
                    elif k in CONDUCTIVE:
                        self.net_sources[nid].append((dpos, d))
                        for s in self._strong_srcs.get(n, ()):
                            self.nets_driven_by[s].add(nid)

        # component -> what it listens to, and the reverse maps
        self.comps_by_net = defaultdict(set)
        self.comps_by_comp = defaultdict(set)
        for cpos in self.components:
            for src_pos in self._input_positions(cpos):
                k = w.kind(src_pos)
                if k == "redstone_wire":
                    self.comps_by_net[self.net_of[src_pos]].add(cpos)
                elif k in SOURCE_KINDS:
                    self.comps_by_comp[src_pos].add(cpos)
                elif k in CONDUCTIVE:
                    for s in self._strong_srcs.get(src_pos, ()):
                        self.comps_by_comp[s].add(cpos)
                    for nid in self._weak_srcs.get(src_pos, ()):
                        self.comps_by_net[nid].add(cpos)

    def _input_positions(self, cpos):
        """Positions a component reads from."""
        b = self.w.blocks[cpos]
        if b.kind == "redstone_torch":
            return [step(cpos, b.attach)]
        if b.kind in ("repeater", "comparator"):
            back = step(cpos, OPPOSITE[b.facing])
            sides = [step(cpos, d) for d in HORIZONTAL
                     if AXIS[d] != AXIS[b.facing]]
            return [back] + sides
        if b.kind == "lamp":
            return [step(cpos, d) for d in ALL6]
        return []

    # ---- power queries ----
    def strong_power(self, pos):
        w, p = self.w, 0
        for spos in self._strong_srcs.get(pos, ()):
            b = w.blocks[spos]
            if b.kind == "redstone_torch":
                if b.lit:
                    p = 15
            elif b.kind == "repeater":
                if b.powered:
                    p = 15
            elif b.kind == "comparator":
                p = max(p, b.out)
            elif b.kind == "lever":
                if b.on:
                    p = 15
            if p == 15:
                break
        return p

    def weak_power(self, pos):
        """Power a block receives from redstone dust resting on or pointing into it."""
        p = 0
        w = self.w
        up = add(pos, UP)
        b = w.get(up)
        if b and b.kind == "redstone_wire":
            p = max(p, b.power)
        for d in HORIZONTAL:
            n = step(pos, d)
            nb = w.get(n)
            if nb and nb.kind == "redstone_wire" and nb.power > 0:
                if self.points[n][OPPOSITE[d]]:
                    p = max(p, nb.power)
        return p

    def block_power(self, pos):
        """Power visible to a *component* reading this block (strong or weak)."""
        return max(self.strong_power(pos), self.weak_power(pos))

    def _dust_source_power(self, dpos, d):
        """Power delivered into dust at `dpos` from its neighbour toward `d`."""
        w = self.w
        n = step(dpos, d)
        b = w.get(n)
        if b is None:
            return 0
        k = b.kind
        if k == "redstone_torch":
            return 15 if b.lit else 0
        if k == "lever":
            return 15 if b.on else 0
        if k == "redstone_block":
            return 15
        if k == "repeater":
            return 15 if (b.powered and step(n, b.facing) == dpos) else 0
        if k == "comparator":
            return b.out if step(n, b.facing) == dpos else 0
        if k in CONDUCTIVE:
            return self.strong_power(n)   # weak power does not reach dust
        return 0

    # ---- net evaluation ----
    def _recompute_net(self, nid):
        w = self.w
        members = self.nets[nid]
        best = {}
        heap = []
        for dpos, d in self.net_sources.get(nid, ()):
            v = self._dust_source_power(dpos, d)
            if v > best.get(dpos, 0):
                best[dpos] = v
        for dpos, v in best.items():
            heap.append((-v, dpos))
        heapq.heapify(heap)
        while heap:
            negv, dpos = heapq.heappop(heap)
            v = -negv
            if v < best.get(dpos, 0) or v <= 1:
                continue
            for nxt in self.dust_links[dpos]:
                if v - 1 > best.get(nxt, 0):
                    best[nxt] = v - 1
                    heapq.heappush(heap, (-(v - 1), nxt))
        changed = False
        for dpos in members:
            v = best.get(dpos, 0)
            blk = w.blocks[dpos]
            if blk.power != v:
                blk.power = v
                changed = True
        return changed

    # ---- component evaluation ----
    def _desired(self, cpos):
        w = self.w
        b = w.blocks[cpos]
        if b.kind == "redstone_torch":
            return self.block_power(step(cpos, b.attach)) == 0
        if b.kind == "repeater":
            back = step(cpos, OPPOSITE[b.facing])
            return self._input_power(back) > 0
        if b.kind == "comparator":
            back = step(cpos, OPPOSITE[b.facing])
            rear = self._input_power(back)
            sides = 0
            for d in HORIZONTAL:
                if AXIS[d] == AXIS[b.facing]:
                    continue
                sides = max(sides, self._side_power(cpos, d))
            if b.mode == "subtract":
                return max(rear - sides, 0)
            return rear if sides <= rear else 0
        if b.kind == "lamp":
            return self._lamp_powered(cpos)
        return None

    def _input_power(self, pos):
        """Power a repeater/comparator reads from the block at `pos`."""
        w = self.w
        b = w.get(pos)
        if b is None:
            return 0
        k = b.kind
        if k == "redstone_wire":
            return b.power
        if k == "redstone_torch":
            return 15 if b.lit else 0
        if k == "lever":
            return 15 if b.on else 0
        if k == "redstone_block":
            return 15
        if k == "repeater":
            return 15 if b.powered else 0
        if k == "comparator":
            return b.out
        if k in CONDUCTIVE:
            return self.block_power(pos)
        return 0

    def _side_power(self, cpos, d):
        """Comparator side input: only direct redstone sources register."""
        w = self.w
        n = step(cpos, d)
        b = w.get(n)
        if b is None:
            return 0
        k = b.kind
        if k == "redstone_wire":
            return b.power
        if k == "redstone_block":
            return 15
        if k == "redstone_torch":
            return 15 if b.lit else 0
        if k == "repeater":
            return 15 if (b.powered and step(n, b.facing) == cpos) else 0
        if k == "comparator":
            return b.out if step(n, b.facing) == cpos else 0
        return 0

    def _lamp_powered(self, cpos):
        w = self.w
        for d in ALL6:
            n = step(cpos, d)
            b = w.get(n)
            if b is None:
                continue
            k = b.kind
            if k == "redstone_wire" and b.power > 0:
                if d == "up" or (d in HORIZONTAL and self.points[n][OPPOSITE[d]]):
                    return True
                if d == "down":
                    continue
            elif k == "redstone_torch" and b.lit:
                return True
            elif k == "lever" and b.on:
                return True
            elif k == "redstone_block":
                return True
            elif k == "repeater" and b.powered and step(n, b.facing) == cpos:
                return True
            elif k == "comparator" and b.out > 0 and step(n, b.facing) == cpos:
                return True
            elif k in CONDUCTIVE and self.block_power(n) > 0:
                return True
        return False

    def _repeater_locked(self, cpos):
        w = self.w
        b = w.blocks[cpos]
        for d in HORIZONTAL:
            if AXIS[d] == AXIS[b.facing]:
                continue
            n = step(cpos, d)
            nb = w.get(n)
            if nb is None:
                continue
            if nb.kind == "repeater" and nb.powered and step(n, nb.facing) == cpos:
                return True
            if nb.kind == "comparator" and nb.out > 0 and step(n, nb.facing) == cpos:
                return True
        return False

    def _current(self, cpos):
        b = self.w.blocks[cpos]
        if b.kind == "redstone_torch":
            return b.lit
        if b.kind == "repeater":
            return b.powered
        if b.kind == "comparator":
            return b.out
        if b.kind == "lamp":
            return b.power > 0
        return None

    def _apply(self, cpos, target):
        b = self.w.blocks[cpos]
        if b.kind == "redstone_torch":
            b.lit = target
        elif b.kind == "repeater":
            b.powered = target
        elif b.kind == "comparator":
            b.out = target
        elif b.kind == "lamp":
            b.power = 15 if target else 0

    def _delay_of(self, cpos):
        b = self.w.blocks[cpos]
        if b.kind == "redstone_torch":
            return TORCH_DELAY
        if b.kind == "repeater":
            return 2 * b.delay
        if b.kind == "comparator":
            return COMPARATOR_DELAY
        return 0

    def _schedule(self, cpos, target):
        if self._pending.get(cpos, self._current(cpos)) == target:
            return
        delay = self._delay_of(cpos)
        if delay == 0:
            self._apply(cpos, target)
            self._dirty_from(cpos)
            return
        self._pending[cpos] = target
        self._seq += 1
        heapq.heappush(self._queue, (self.now + delay, self._seq, cpos, target))

    # ---- settling ----
    def _dirty_from(self, cpos):
        self._dirty_nets.update(self.nets_driven_by.get(cpos, ()))
        self._dirty_comps.update(self.comps_by_comp.get(cpos, ()))

    def settle(self, changed=None):
        """Propagate dust power and re-evaluate components to a fixed point."""
        self._dirty_nets = set()
        self._dirty_comps = set()
        if changed is None:
            self._dirty_nets = set(range(len(self.nets)))
            self._dirty_comps = set(self.components)
        else:
            for c in changed:
                self._dirty_from(c)

        guard = 0
        while self._dirty_nets or self._dirty_comps:
            guard += 1
            if guard > 10000:
                raise RuntimeError("redstone failed to settle (combinational loop?)")
            nets = self._dirty_nets
            self._dirty_nets = set()
            for nid in nets:
                if self._recompute_net(nid):
                    self._dirty_comps.update(self.comps_by_net.get(nid, ()))
                else:
                    # first pass still needs listeners evaluated
                    if changed is None:
                        self._dirty_comps.update(self.comps_by_net.get(nid, ()))
            comps = self._dirty_comps
            self._dirty_comps = set()
            for cpos in comps:
                b = self.w.blocks[cpos]
                if b.kind == "repeater":
                    locked = self._repeater_locked(cpos)
                    b.locked = locked
                    if locked:
                        continue
                if b.kind == "redstone_torch" and cpos in self.burned_out:
                    continue
                self._schedule(cpos, self._desired(cpos))

    def tick(self):
        """Advance one game tick."""
        self.now += 1
        fired = []
        while self._queue and self._queue[0][0] <= self.now:
            _, _, cpos, target = heapq.heappop(self._queue)
            if self._pending.get(cpos) != target:
                continue
            del self._pending[cpos]
            if self._current(cpos) == target:
                continue
            b = self.w.blocks[cpos]
            if b.kind == "redstone_torch" and self.model_burnout:
                b._toggles.append(self.now)
                b._toggles = [t for t in b._toggles if t > self.now - BURNOUT_WINDOW]
                if len(b._toggles) > BURNOUT_LIMIT:
                    self.burned_out.add(cpos)
                    b.lit = False
                    fired.append(cpos)
                    continue
            self._apply(cpos, target)
            fired.append(cpos)
        if fired:
            self.settle(fired)
        return fired

    def initialize_steady(self, max_iters=2000):
        """Relax straight to the circuit's steady state, skipping power-on.

        Instantiating a large build all at once starts every torch lit, and the
        resulting transient sweeping through a deep network can toggle a torch
        more than eight times in 60 ticks and burn it out — an artefact of
        materialising the machine in one instant rather than building it up.
        This settles the (acyclic) logic combinationally, then clears the queue
        and the burnout history so timing measurements start from rest.
        """
        for _ in range(max_iters):
            changed = False
            for nid in range(len(self.nets)):
                if self._recompute_net(nid):
                    changed = True
            for cpos in self.components:
                b = self.w.blocks[cpos]
                if b.kind == "repeater":
                    b.locked = self._repeater_locked(cpos)
                    if b.locked:
                        continue
                want = self._desired(cpos)
                if self._current(cpos) != want:
                    self._apply(cpos, want)
                    changed = True
            if not changed:
                break
        else:
            raise RuntimeError("circuit has no steady state (feedback loop?)")
        self._queue.clear()
        self._pending.clear()
        self.burned_out.clear()
        for b in self.w.blocks.values():
            if b.kind == "redstone_torch":
                b._toggles.clear()
        return self

    def run(self, ticks):
        for _ in range(ticks):
            self.tick()

    def run_until_stable(self, max_ticks=2000):
        """Run until no events remain. Returns elapsed game ticks."""
        start = self.now
        n = 0
        while self._queue and n < max_ticks:
            self.tick()
            n += 1
        if self._queue:
            raise RuntimeError("circuit did not stabilise (oscillator?)")
        return self.now - start

    # ---- convenience I/O ----
    def set_lever(self, pos, on):
        b = self.w.blocks[pos]
        if b.on == on:
            return
        b.on = on
        self.settle([pos])

    def read(self, pos):
        b = self.w.blocks[pos]
        if b.kind == "redstone_wire":
            return b.power
        if b.kind == "redstone_torch":
            return 15 if b.lit else 0
        if b.kind == "repeater":
            return 15 if b.powered else 0
        if b.kind == "comparator":
            return b.out
        if b.kind == "lamp":
            return b.power
        if b.kind == "lever":
            return 15 if b.on else 0
        if b.kind in CONDUCTIVE:
            return self.block_power(pos)
        return 0

    def high(self, pos):
        return self.read(pos) > 0
