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
#: X spacing between collectors. Three is the floor and the floor is reachable:
#: a gate owns its collector column and the tap column beside it, and the tap's
#: strongly powered block must not touch the *next* gate's collector. At 3 it
#: does not — the next collector is two columns away — so the fourth column was
#: only ever slack. Taking it out costs the rails their block-repeater-block
#: sandwiches, since three free cells in a row no longer exist between taps, and
#: a bare repeater carries 16 blocks per redstone tick against the sandwich's
#: 18. The rails get a quarter shorter in exchange, which is the better trade in
#: both directions: measured on a 6-bit ALU, 9% fewer blocks and 13% *less*
#: latency. See §18.
GATE_PITCH = 3
STAGE_DY = 4         # Y per logic stage
MAX_RUN = 10         # collector: max blocks from a tap to the next repeater
EXIT_GAP = 4         # Z clearance between a gate's last tap and its exit


class Layout:
    def __init__(self, world: World):
        self.w = world
        self.pos: dict[int, tuple] = {}      # node idx -> rail start (x, y, z)
        self.levers: dict[str, tuple] = {}
        self.outputs: dict[str, tuple] = {}
        self.exit_step: dict[int, int] = {}  # gate idx -> which end it is read at
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
                    repeater_delay=1, drive_inputs=True,
                    fixed_input_order=False, alternate=False) -> Layout:
    """Compile `nl` into blocks in `world`.

    `alternate` reads each stage's collectors at the opposite end from the one
    below, which stops Z ratcheting with depth (`_assign_exits`). It is off by
    default because it is not free and does not always pay: it trades hazard
    margin for depth, and the depth is only worth having once there is enough
    of it. Measured on four builds — §18 has the table — it takes 47% of the Z
    off the 36-stage Mk III and makes it faster, and costs the 12-stage Mk I
    ALU 60% of its latency for 29% of a Z it was not short of. Turn it on when
    the machine is deep. `build_machine` does.
    """
    world = world or World()
    L = Layout(world)
    prune(nl)
    order = order_stages(nl)
    if fixed_input_order:
        # keep primary inputs in declaration order, so anything hand-built that
        # has to drive them (a keypad, say) gets a predictable rail layout
        rank = {id(n): i for i, n in enumerate(nl.inputs.values())}
        order[0].sort(key=lambda n: rank.get(id(n), len(rank)))

    depth = nl.depth()
    y = 0

    # stage 1's rails are the primary inputs, driven by levers
    for i, n in enumerate(order[0]):
        L.pos[n.idx] = (0, y, RAIL_PITCH * i)
        L.power[n.idx] = 14          # lever sits one block back from the rail

    # Which end each stage's collectors are read at, alternating so Z stops
    # ratcheting (`_assign_exits`). A stage's taps sit on the side the stage
    # below it left by; stage 1 reads levers, which have no riser to avoid, so
    # it starts on the +Z side and the alternation follows from there.
    exit_step = {s: (1 if (s % 2 or not alternate) else -1)
                 for s in range(1, depth + 1)}
    tap_side = {s: (1 if s == 1 else exit_step[s - 1]) for s in
                range(1, depth + 1)}

    for s in range(1, depth + 1):
        gates = [n for n in order[s] if n.kind == "gate"]
        if not gates:
            continue
        rails = _rails_for_stage(nl, s)
        step, side = exit_step[s], tap_side[s]

        # ---- X: one column per gate, in the stage's chosen order
        for i, g in enumerate(gates):
            L.pos[g.idx] = (GATE_PITCH * i, y + 2, None)

        # ---- exit Z: just past each gate's last tap, on the rail grid
        for idx, slot in _assign_exits(L, gates, step, side).items():
            x = L.pos[idx][0]
            L.pos[idx] = (x, y + STAGE_DY, slot)
            L.exit_step[idx] = step

        # ---- rails
        consumers: dict[int, list] = defaultdict(list)
        for g in gates:
            for src, _ in g.taps:
                consumers[src].append(g)
        for r in rails:
            rx, _, rz = L.pos[r.idx]
            xs = [L.pos[g.idx][0] for g in consumers[r.idx]]
            # only the columns where THIS rail is tapped are off limits; other
            # gates read their own rails, at other Z
            own_taps = {x + 1 for x in xs}
            _place_rail(L, rx, min(xs), max(xs) + 2, y, rz,
                        own_taps, repeater_delay, L.power[r.idx])
            if r.kind == "input" and s == 1:
                if drive_inputs:
                    _place_lever(L, r.name, rx, y, rz)
                else:
                    L.levers[r.name] = (rx, y, rz)   # driven from outside

        # ---- collectors, taps and the climb to the next rail plane
        for g in gates:
            gx, _, rail_z = L.pos[g.idx]
            tap_zs = [L.pos[src][2] + 2 * side for src, _ in g.taps]
            z_exit = rail_z - 2 * step
            z_from = min(tap_zs) if step > 0 else max(tap_zs)
            L.power[g.idx] = _place_collector(
                L, gx, y + 2, z_from, z_exit, tap_zs, repeater_delay, step)
            for src, inv in g.taps:
                _place_tap(L, gx, y, L.pos[src][2], inv, repeater_delay, side)
            _place_riser(L, gx, y + 2, z_exit, step)

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


def _tap_span(L: Layout, g, side=1):
    zs = [L.pos[src][2] + 2 * side for src, _ in g.taps]
    return min(zs), max(zs)


def _up(z):
    return -(-z // RAIL_PITCH) * RAIL_PITCH          # round up to the grid


def _down(z):
    return (z // RAIL_PITCH) * RAIL_PITCH            # round down to the grid


def _assign_exits(L: Layout, gates, step, side):
    """Give every gate in a stage an exit slot on the rail grid.

    A collector is read at one end, and that end has to sit past the gate's own
    last tap — but *which* end is free, and always choosing the same one is what
    §12's Z ratchet was made of. Read every collector at its +Z end and every
    exit lands above every tap; the next stage's rails are those exits, so its
    taps are higher still, and Z climbs by the width of a stage, every stage,
    and never comes back. Over 36 stages that was three quarters of the
    machine's depth — and depth is collector, collector is repeaters, and
    repeaters are the latency.

    So `step` alternates: +1 on one stage, -1 on the next. The band of Z the
    machine occupies then oscillates inside roughly twice the widest stage
    instead of accumulating every stage's width in turn.

    It has to be the whole stage, not gate by gate. A gate's riser climbs to the
    rail plane through the cell one *behind* its exit, and that cell has to stay
    clear for the dust to step up through it — which is exactly where the next
    stage's taps would sit if they came off the same side. Taps therefore go on
    the side the producing stage left by, and a stage whose rails were produced
    both ways would need taps on both sides of neighbouring rails, which
    collide at `RAIL_PITCH` 4. One direction per stage keeps every rail's taps
    on one side and the pitch unchanged.

    Returns ``{gate index: slot}``.
    """
    span = {g.idx: _tap_span(L, g, side) for g in gates}
    pref = {g.idx: (_up(span[i][1] + EXIT_GAP) if step > 0
                    else _down(span[i][0] - EXIT_GAP))
            for g in gates for i in (g.idx,)}
    taken, out = set(), {}
    # hand them out from the tap span outwards, so a gate is only pushed past
    # its neighbour when it genuinely collides
    for g in sorted(gates, key=lambda g: step * pref[g.idx]):
        slot = pref[g.idx]
        while slot in taken:
            slot += step * RAIL_PITCH
        taken.add(slot)
        out[g.idx] = slot
    return out


# --- placement --------------------------------------------------------------

def plan_run(cells, taps, src_power, min_at_tap=2):
    """Decide where repeaters go along a run, as late as strength allows.

    Returns {position: "dust" | "blockA" | "rep" | "blockB"}, or None.

    Repeaters go in as a *sandwich* — solid block, repeater, solid block. The
    block in front is weakly powered by the arriving dust, which a repeater
    reads happily, and the block behind is strongly powered so the next run of
    dust restarts at a full 15. That is 18 blocks per redstone tick against 16
    for a bare in-line repeater, and it is the wiki's standard transmission
    line for exactly that reason.

    A solid block only works as the sandwich's front half if the dust before it
    *points* at it, and dust only points where it connects. Two cells fail that
    test: one carrying a tap, which already connects sideways to the tap's
    riser, and the run's own source cell, which connects down to the riser that
    fed it. In both cases the block, and everything past it, goes dead. The
    sandwich therefore never starts on the first cell or straight after a tap.
    A bare repeater is fine in either spot, since a repeater reads dust
    directly rather than through a block.

    Placing them purely greedily does not work: a repeater cannot sit on a cell
    where the run is tapped, so waiting until the signal is nearly spent can
    arrive at a tap column with nothing left and nowhere legal to repeat. The
    trigger is therefore relaxed until a whole run is feasible — latest
    possible placement that still never starves a tap.
    """
    for trigger in (2, 3, 4, 5, 6, 8, 10, 13):
        plan, p, i, ok = {}, src_power, 0, True
        while i < len(cells):
            x = cells[i]
            need = min_at_tap if x in taps else 1
            if p - 1 < need or p <= trigger:
                three = cells[i:i + 3]
                prev_ok = i > 0 and cells[i - 1] not in taps
                if (len(three) == 3 and prev_ok and p >= 1
                        and not any(c in taps for c in three)):
                    plan[three[0]] = "blockA"
                    plan[three[1]] = "rep"
                    plan[three[2]] = "blockB"
                    p, i = 16, i + 3
                    continue
                if x not in taps and p >= 1:
                    plan[x] = "rep"
                    p, i = 16, i + 1
                    continue
            if p - 1 < need:
                ok = False
                break
            plan[x] = "dust"
            p -= 1
            i += 1
        if ok:
            return plan
    return None


def _emit_run(L, plan, cells, y, coord, direction):
    """Realise a plan along a run. `coord(x)` maps a step to a world position."""
    for x in cells:
        pos = coord(x)
        L._floor((pos[0], y - 1, pos[2]))
        kind = plan[x]
        if kind == "dust":
            L._dust(pos)
        elif kind == "rep":
            L._repeater(pos, direction, L.delay)
        else:
            L._solid(pos)


def _place_rail(L: Layout, x_src, x_lo, x_hi, y, z, tap_x, delay, src_power):
    """Dust along X, fed from `x_src`, running outwards in both directions."""
    x_lo = min(x_lo, x_src)
    x_hi = max(x_hi, x_src)
    L.delay = delay
    L._floor((x_src, y - 1, z))
    L._dust((x_src, y, z))
    for direction, stepd in (("east", 1), ("west", -1)):
        end = x_hi if stepd > 0 else x_lo
        cells = list(range(x_src + stepd, end + stepd, stepd))
        if not cells:
            continue
        plan = plan_run(cells, tap_x, src_power)
        assert plan is not None, f"rail at z={z} cannot be routed"
        _emit_run(L, plan, cells, y, lambda x: (x, y, z), direction)


def plan_collector(cells, taps, max_span=13):
    """Where repeaters go along a collector.

    A collector is a wired-OR with many sources, so there is no single strength
    to budget. A tap only injects when its own rail is high, which means every
    tap has to reach the next repeater *alone* — the binding case is always the
    earliest tap after the last repeater. Measuring the span from the last
    repeater covers that, and a non-inverting tap only injects 14 (it arrives
    through a dust step), so the span is 13.

    Repeaters use the same block-repeater-block sandwich as the rails, which is
    why a run reaches 16 cells per redstone tick rather than 11.

    A solid block only works as the sandwich's front half if the dust before it
    *points* at it, and dust only points where it connects. Two cells fail that
    test: one carrying a tap, which already connects sideways to the tap's
    riser, and the run's own source cell, which connects down to the riser that
    fed it. In both cases the block, and everything past it, goes dead. The
    sandwich therefore never starts on the first cell or straight after a tap.
    A bare repeater is fine in either spot, since a repeater reads dust
    directly rather than through a block.
    """
    plan, since, i = {}, 0, 0
    while i < len(cells):
        z = cells[i]
        if z not in taps and since >= max_span:
            three = cells[i:i + 3]
            # never let the sandwich's trailing block land on the exit cell:
            # the riser reads the exit, and a plain solid block there conducts
            # nothing onward
            room = i + 2 <= len(cells) - 2
            prev_ok = i > 0 and cells[i - 1] not in taps
            if (len(three) == 3 and room and prev_ok
                    and not any(c in taps for c in three)):
                plan[three[0]] = "blockA"
                plan[three[1]] = "rep"
                plan[three[2]] = "blockB"
                since, i = 0, i + 3
                continue
            plan[z] = "rep"
            since, i = 0, i + 1
            continue
        plan[z] = "dust"
        since += 1
        i += 1
    return plan


def _worst_exit_power(plan, cells, taps):
    """Strength reaching the far end when only one tap is on — the binding case.

    Taps that share a downstream repeater all arrive as that repeater's clean
    15, so only the last repeater and any taps after it can be the weakest.

    Distances are counted in *positions along the run*, not in Z, because a
    collector read at its -Z end runs the other way.
    """
    end = len(cells) - 1
    last_rep = None
    for i, z in enumerate(cells):
        if plan[z] in ("rep", "blockB"):
            last_rep = i
    worst = []
    if last_rep is not None:
        worst.append(15 - (end - (last_rep + 1)))
    for i, z in enumerate(cells):
        if z in taps and (last_rep is None or i > last_rep):
            worst.append(14 - (end - i))
    return min(worst) if worst else 0


def _place_collector(L: Layout, x, y, z0, z1, tap_zs, delay, step=1):
    """Dust along Z at `x`; the wired-OR is read at the `step` end.

    `step` is +1 for a collector read at its +Z end and -1 for one read at its
    -Z end — see `_assign_exits` for why both exist. `z0` is the far end from
    the exit, so it is the gate's lowest tap going up and its highest going
    down.

    Returns the strength the next rail starts from, so it can budget its own
    repeaters honestly instead of assuming a full 15.
    """
    L.delay = delay
    cells = list(range(z0, z1 + step, step))
    taps = set(tap_zs)
    plan = plan_collector(cells, taps)

    worst = _worst_exit_power(plan, cells, taps)
    # The riser costs two levels on its way to the rail plane, so a run that
    # arrives weak leaves the rail with nothing. Terminate it with a repeater
    # when that would happen — it costs a tick, but only where it is needed.
    if worst - 2 < 6 and cells[-1] not in taps:
        # The repeater has to sit on the exit cell itself: the riser's own
        # support block is what it strongly powers, and that block is what the
        # riser dust reads. Put a solid block behind it where there is room and
        # the pair becomes the same sandwich the rest of the run uses.
        plan[cells[-1]] = "rep"
        if len(cells) > 1 and cells[-2] not in taps:
            plan[cells[-2]] = "blockA"
        worst = 16
    _emit_run(L, plan, cells, y, lambda z: (x, y, z),
              "south" if step > 0 else "north")
    return 14 if plan[cells[-1]] == "rep" else max(worst - 2, 1)


def _place_tap(L: Layout, gx, y, rail_z, inverting, delay, side=1):
    """Join a rail to a collector, in either polarity.

    `side` is which way along Z the tap reaches out from its rail — +1 or -1.
    It is not a free choice per tap: it has to be the side the stage below left
    by, because the other side is where that stage's risers climb. See
    `_assign_exits`.

    The inverting tap reads the rail through a stub of dust, which hands its
    torch every hazard the rail has, and those torches are where nearly all of
    §13's burnout happens. Putting a repeater in the stub's cell instead — free,
    since the non-inverting tap already has one there — was measured across
    three repeater settings and three hostile sequences: worse at delay 2, a
    wash at delay 3, and 3% slower at delay 4 for nothing. §17 has the table.
    The stub stays.
    """
    tx = gx + 1
    z1, z2 = rail_z + side, rail_z + 2 * side
    L.stats["taps"] += 1
    L._floor((tx, y - 1, z1))
    L._floor((tx, y - 1, z2))
    if inverting:
        L._dust((tx, y, z1))                          # stub, points into base
        L._solid((tx, y, z2))                         # base
        L.w.torch((tx, y + 1, z2), attach="down")
        L.stats["torches"] += 1
        L._solid((tx, y + 2, z2))                     # strongly powered
    else:
        L._repeater((tx, y, z1), "south" if side > 0 else "north", delay)
        L._solid((tx, y, z2))
        L._dust((tx, y + 1, z2))                      # climbs into collector


def _place_riser(L: Layout, x, y, z_exit, step=1):
    """Two dust steps carrying a collector's value up to the next rail plane.

    It climbs the way its collector runs, so a collector read at its -Z end puts
    the rail it becomes two blocks *below* the exit rather than above.
    """
    L._solid((x, y, z_exit + step))
    L._dust((x, y + 1, z_exit + step))
    L._solid((x, y + 1, z_exit + 2 * step))
    L._dust((x, y + 2, z_exit + 2 * step))
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
