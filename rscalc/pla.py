"""Place-and-route: compile a `Netlist` into actual redstone blocks.

Physical architecture — a stacked PLA, four Y levels per logic stage:

    y+4   next stage's rails            <- reached by a dust staircase
    y+2   collectors  (dust along +Z, one per gate)
    y+0   rails       (dust along +X, one per signal)

  * a **tap** is the crossbar element joining a rail to a collector::

        inverting (torch)                 non-inverting (repeater)
        y+2  [solid]---collector          y+2         ---collector
        y+1  [torch]                      y+1  [dust] /  (dust staircase)
        y+0  [base] <- [stub] <- rail     y+0  [solid] <- [rep] <- rail

    Either polarity is available, so a gate realises *OR of arbitrary
    literals*: any two-level sum-of-products costs two stages, and each stage
    costs one redstone tick.

  * a collector is a wired-OR read at its +Z end, so it must physically reach
    past its last tap; it then climbs two levels to become the next stage's
    rail. Keeping rails on their own plane (rather than sharing the collector
    plane) means a collector can never short against a rail, which removes any
    ordering constraint between them. Collectors therefore stop just past their
    last tap instead of running the length of the machine, and rails may feed
    outwards in both directions from their source.

Signal strength is the binding constraint throughout: dust fades one level per
block, so both rails and collectors carry repeaters. Distance is always
measured from the last *repeater*, never from an intervening tap — a tap only
injects when its own rail is high, so any single active tap must reach the end
on its own.
"""

from __future__ import annotations

from collections import defaultdict

from .engine import World
from .netlist import Netlist

RAIL_PITCH = 4       # Z spacing between rails
GATE_PITCH = 4       # X spacing between collectors
STAGE_DY = 4         # Y per logic stage
MAX_RUN = 10         # collector: max blocks from a tap to the next repeater
EXIT_GAP = 4         # Z clearance between a gate's last tap and its exit


class Layout:
    def __init__(self, world: World):
        self.w = world
        self.pos: dict[int, tuple] = {}      # node idx -> rail start (x, y, z)
        self.levers: dict[str, tuple] = {}
        self.outputs: dict[str, tuple] = {}
        self.power: dict[int, int] = {}      # node idx -> strength at its source
        self.stats = defaultdict(int)

    def _floor(self, p):
        if self.w.get(p) is None:
            self.w.solid(p)

    def _dust(self, p):
        b = self.w.get(p)
        if b is None:
            self.w.wire(p); self.stats["dust"] += 1
        elif b.kind != "redstone_wire":
            raise AssertionError(f"cannot put dust at {p}: {b.kind} there")

    def _solid(self, p):
        b = self.w.get(p)
        if b is None:
            self.w.solid(p)
        elif b.kind != "solid":
            raise AssertionError(f"cannot put solid at {p}: {b.kind} there")

    def _repeater(self, p, facing, delay):
        if self.w.get(p) is not None:
            raise AssertionError(f"cannot put repeater at {p}")
        self.w.repeater(p, facing=facing, delay=delay)
        self.stats["repeaters"] += 1


def compile_netlist(nl: Netlist, world: World | None = None,
                    repeater_delay=1) -> Layout:
    world = world or World()
    L = Layout(world)
    prune(nl)
    order = order_stages(nl)

    depth = nl.depth()
    y = 0

    # stage 1's rails are the primary inputs, driven by levers
    for i, n in enumerate(order[0]):
        L.pos[n.idx] = (0, y, RAIL_PITCH * i)
        L.power[n.idx] = 14          # lever sits one block back from the rail

    for s in range(1, depth + 1):
        gates = [n for n in order[s] if n.kind == "gate"]
        if not gates:
            continue
        rails = _rails_for_stage(nl, s)

        # ---- X: one column per gate, in the stage's chosen order
        for i, g in enumerate(gates):
            L.pos[g.idx] = (GATE_PITCH * i, y + 2, None)

        # ---- exit Z: just past each gate's last tap, on the rail grid
        taken = set()
        for g in sorted(gates, key=lambda g: _max_tap_z(L, g)):
            want = _max_tap_z(L, g) + EXIT_GAP
            slot = -(-want // RAIL_PITCH) * RAIL_PITCH        # round up
            while slot in taken:
                slot += RAIL_PITCH
            taken.add(slot)
            x = L.pos[g.idx][0]
            L.pos[g.idx] = (x, y + STAGE_DY, slot)

        # ---- rails
        consumers: dict[int, list] = defaultdict(list)
        for g in gates:
            for src, _ in g.taps:
                consumers[src].append(g)
        tap_x = {L.pos[g.idx][0] + 1 for g in gates}

        for r in rails:
            rx, _, rz = L.pos[r.idx]
            xs = [L.pos[g.idx][0] for g in consumers[r.idx]]
            _place_rail(L, rx, min(xs), max(xs) + 2, y, rz,
                        tap_x, repeater_delay, L.power[r.idx])
            if r.kind == "input" and s == 1:
                _place_lever(L, r.name, rx, y, rz)

        # ---- collectors, taps and the climb to the next rail plane
        for g in gates:
            gx, _, rail_z = L.pos[g.idx]
            tap_zs = [L.pos[src][2] + 2 for src, _ in g.taps]
            z_exit = rail_z - 2
            L.power[g.idx] = _place_collector(
                L, gx, y + 2, min(tap_zs), z_exit, tap_zs, repeater_delay)
            for src, inv in g.taps:
                _place_tap(L, gx, y, L.pos[src][2], inv, repeater_delay)
            _place_riser(L, gx, y + 2, z_exit)

        y += STAGE_DY

    for name, node in nl.outputs.items():
        if node.idx in L.pos:
            L.outputs[name] = L.pos[node.idx]
    return L


# --- ordering ---------------------------------------------------------------

def order_stages(nl: Netlist, sweeps=6):
    """Order each stage so that a gate's taps sit close together in Z.

    Straight barycentre relaxation, the standard layered-graph heuristic:
    a gate is drawn toward the mean position of the rails it taps, and a rail
    toward the mean position of the gates that tap it. Shorter tap spans mean
    shorter collectors, which means fewer repeaters, which means less delay.
    """
    depth = nl.depth()
    order = [[n for n in nl.nodes if n.stage == s] for s in range(depth + 1)]
    consumers = defaultdict(list)
    for n in nl.nodes:
        for src, _ in n.taps:
            consumers[src].append(n.idx)

    for _ in range(sweeps):
        # forward: place gates near the rails they read
        for s in range(1, depth + 1):
            idx = {n.idx: i for i, n in enumerate(order[s - 1])}
            order[s].sort(key=lambda g: (
                sum(idx.get(src, 0) for src, _ in g.taps) / max(len(g.taps), 1)))
        # backward: place rails near the gates that read them
        for s in range(depth - 1, -1, -1):
            idx = {n.idx: i for i, n in enumerate(order[s + 1])}
            order[s].sort(key=lambda r: (
                sum(idx.get(c, 0) for c in consumers[r.idx]) /
                max(len(consumers[r.idx]), 1)) if consumers[r.idx] else 1e9)
    return order


def _rails_for_stage(nl: Netlist, s: int):
    seen, out = set(), []
    for n in nl.nodes:
        if n.stage != s or n.kind != "gate":
            continue
        for src, _ in n.taps:
            if src not in seen:
                seen.add(src)
                out.append(nl.nodes[src])
    return out


def _max_tap_z(L: Layout, g):
    return max(L.pos[src][2] for src, _ in g.taps) + 2


# --- placement --------------------------------------------------------------

def _place_rail(L: Layout, x_src, x_lo, x_hi, y, z, tap_x, delay, src_power):
    """Dust along X, fed from `x_src`, running outwards in both directions.

    Repeaters are spent against an actual signal-strength budget rather than a
    fixed spacing: one goes in only when the dust would otherwise fall too weak
    to drive a tap (a tap needs 2 at the rail, since its stub is one further
    on). Every repeater costs a redstone tick, so not placing one is a direct
    saving on the machine's latency.
    """
    x_lo = min(x_lo, x_src)
    x_hi = max(x_hi, x_src)
    L._floor((x_src, y - 1, z))
    L._dust((x_src, y, z))
    for direction, rng in (("east", range(x_src + 1, x_hi + 1)),
                           ("west", range(x_src - 1, x_lo - 1, -1))):
        p = src_power
        for x in rng:
            L._floor((x, y - 1, z))
            if p - 1 < 3 and x not in tap_x:
                L._repeater((x, y, z), direction, delay)
                p = 15
            else:
                L._dust((x, y, z))
                p = max(p - 1, 0)


def _place_collector(L: Layout, x, y, z0, z1, tap_zs, delay):
    """Dust along +Z at `x`; the wired-OR is read at the +Z end.

    A non-inverting tap reaches the collector through a dust step, so it only
    injects 14 — one less than the torch tap's strongly-powered block. The run
    is therefore budgeted against 14, and terminated with a repeater so that
    whatever survives the run leaves as a clean 15.
    """
    srcs = set(tap_zs)
    since, seen = 0, False
    for z in range(z0, z1):
        L._floor((x, y - 1, z))
        if z in srcs:
            L._dust((x, y, z))
            if seen:
                since += 1
            else:
                seen, since = True, 0
        elif seen and since >= MAX_RUN and (z + 1) not in srcs and z != z1:
            L._repeater((x, y, z), "south", delay)
            since = 0
        else:
            L._dust((x, y, z))
            since += 1
    # Terminating repeater: only worth a redstone tick when the run actually
    # needs it. A short collector still leaves enough strength to drive the
    # riser and a modest rail on its own.
    L._floor((x, y - 1, z1))
    if 14 - (since + 1) - 2 >= 8:
        L._dust((x, y, z1))
        return 14 - (since + 1) - 2
    L._repeater((x, y, z1), "south", delay)
    return 14


def _place_tap(L: Layout, gx, y, rail_z, inverting, delay):
    tx = gx + 1
    L.stats["taps"] += 1
    L._floor((tx, y - 1, rail_z + 1))
    L._floor((tx, y - 1, rail_z + 2))
    if inverting:
        L._dust((tx, y, rail_z + 1))                  # stub, points into base
        L._solid((tx, y, rail_z + 2))                 # base
        L.w.torch((tx, y + 1, rail_z + 2), attach="down")
        L.stats["torches"] += 1
        L._solid((tx, y + 2, rail_z + 2))             # strongly powered
    else:
        L._repeater((tx, y, rail_z + 1), "south", delay)
        L._solid((tx, y, rail_z + 2))
        L._dust((tx, y + 1, rail_z + 2))              # climbs into collector


def _place_riser(L: Layout, x, y, z_exit):
    """Two dust steps carrying a collector's value up to the next rail plane."""
    L._solid((x, y, z_exit + 1))
    L._dust((x, y + 1, z_exit + 1))
    L._solid((x, y + 1, z_exit + 2))
    L._dust((x, y + 2, z_exit + 2))
    L.stats["risers"] += 1


def _place_lever(L: Layout, name, x, y, z):
    L._floor((x - 2, y - 1, z))
    L.w.lever((x - 2, y, z), attach="down", on=False)
    L.levers[name] = (x - 2, y, z)
    L._floor((x - 1, y - 1, z))
    L._dust((x - 1, y, z))


def prune(nl: Netlist):
    """Drop gates no output depends on."""
    if not nl.outputs:
        return
    keep, stack = set(), [n.idx for n in nl.outputs.values()]
    while stack:
        i = stack.pop()
        if i in keep:
            continue
        keep.add(i)
        for src, _ in nl.nodes[i].taps:
            stack.append(src)
    nl.nodes = [n for n in nl.nodes if n.idx in keep]
    remap = {n.idx: i for i, n in enumerate(nl.nodes)}
    for n in nl.nodes:
        n.idx = remap[n.idx]
        n.taps = [(remap[s], inv) for s, inv in n.taps]
    alive = {id(n) for n in nl.nodes}
    nl.inputs = {k: v for k, v in nl.inputs.items() if id(v) in alive}
