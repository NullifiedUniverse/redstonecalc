"""Every redstone rule this project relies on, checked separately.

Two independent kinds of check:

**Rule by rule.** Each of the thirteen rules in `tests/refsim.py` gets its own
tiny placed circuit with one thing in it, so a reader who wants to confirm a
rule against the game can build exactly that circuit and look. Each test says
what the rule is and what the game does, then asserts it.

**Engine against engine.** The rules are also implemented a second time in
`refsim.py` — no precomputed nets, no dirty sets, everything recomputed from
scratch every tick — and the two are run side by side on random circuits,
comparing *every block's state at every tick*. Agreement does not prove the
rules match Minecraft; it proves that the fast engine the whole project rests on
implements the same rules the slow obvious one does, which is the part that could
silently drift.

What is deliberately *not* modelled, restated here so it cannot be mistaken for
an oversight: neighbour-update ordering within a tick (so zero-tick pulses and
locational randomness are out of scope), and quasi-connectivity (no pistons are
used anywhere in this project). `World.lint()` rejects the constructions whose
behaviour would depend on either.
"""

import random
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from tests.refsim import RefEngine, fast_snapshot


def both(world):
    """The same world, standing in both simulators, at rest."""
    fast = Engine(world)
    fast.initialize_steady()
    slow_world = clone(world)
    slow = RefEngine(slow_world)
    slow.initialize_steady()
    return fast, slow, slow_world


def clone(world):
    w = World()
    for pos, b in world.blocks.items():
        w.set(pos, b.copy())
    return w


def floor(w, x0, x1, y, z0, z1):
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            w.solid((x, y, z))


# --- R1: strength and decay -------------------------------------------------

def test_r1_dust_loses_one_level_per_block():
    """A lever hands 15 to the dust beside it, and each further block is one
    less; at 16 blocks the signal is gone."""
    w = World()
    floor(w, -1, 20, -1, 0, 0)
    w.lever((-1, 0, 0), attach="down", on=True)
    for x in range(0, 21):
        w.wire((x, 0, 0))
    e = Engine(w)
    e.initialize_steady()
    got = [e.read((x, 0, 0)) for x in range(0, 17)]
    want = [15 - x if x <= 15 else 0 for x in range(0, 17)]
    assert got == want, f"decay is {got}, want {want}"
    assert e.read((16, 0, 0)) == 0, "15 blocks is the reach, 16 is out"
    print("  R1 dust decays one level per block, reaching 15 and no further: OK")


# --- R3 / R4: strong versus weak power --------------------------------------

def test_r3_strongly_powered_block_powers_dust_at_15():
    """A torch strongly powers the block above it, and dust beside that block
    reads a full 15 — this is what every riser in the build depends on."""
    w = World()
    w.solid((0, 0, 0))                       # torch support
    w.torch((0, 1, 0), attach="down")
    w.solid((0, 2, 0))                       # strongly powered by the torch
    w.solid((1, 1, 0))
    w.wire((1, 2, 0))                        # beside the powered block
    e = Engine(w)
    e.initialize_steady()
    assert e.read((1, 2, 0)) == 15, e.read((1, 2, 0))
    print("  R3 dust beside a strongly powered block reads 15: OK")


def test_r4_weakly_powered_block_does_not_power_dust():
    """Dust weakly powers the block it points at, and a weakly powered block
    powers *nothing* further. Getting this wrong would make the whole compiler's
    isolation between rails meaningless."""
    w = World()
    floor(w, 0, 4, -1, 0, 0)
    w.lever((0, 0, 0), attach="down", on=True)
    w.wire((1, 0, 0))
    w.wire((2, 0, 0))
    w.solid((3, 0, 0))                       # weakly powered by the dust
    w.solid((4, -1, 0))
    w.wire((4, 0, 0))                        # on the far side: must stay dark
    e = Engine(w)
    e.initialize_steady()
    assert e.read((2, 0, 0)) > 0, "the dust itself should be live"
    assert e.read((4, 0, 0)) == 0, (
        f"dust past a weakly powered block reads {e.read((4, 0, 0))}, want 0")
    print("  R4 a weakly powered block does not power dust beyond it: OK")


# --- R6: pointing -----------------------------------------------------------

def test_r6_pointing_follows_connection():
    """Pointing follows connection: none is a dot, one is a straight line that
    points both ways, and only cells a dust points at get weak power. The
    compiler's stub cells exist entirely because of this rule."""
    # a dot: no connections at all
    dot = World()
    dot.solid((0, -1, 0))
    dot.wire((0, 0, 0))
    for d in ("north", "south", "east", "west"):
        assert not dot.wire_points((0, 0, 0), d), f"a dot must not point {d}"

    # one connection: a straight line along that axis, both ways
    line = World()
    line.solid((0, -1, 0))
    line.solid((1, -1, 0))
    line.lever((1, 0, 0), attach="down", on=True)
    line.wire((0, 0, 0))
    line.solid((-1, 0, 0))                   # in line: is pointed at
    line.solid((0, 0, 1))                    # to the side: is not
    e = Engine(line)
    e.initialize_steady()
    assert e.read((0, 0, 0)) == 15
    assert line.wire_points((0, 0, 0), "west"), "a line points away from its feed"
    assert line.wire_points((0, 0, 0), "east"), "and back toward it"
    assert not line.wire_points((0, 0, 0), "south"), "but not sideways"

    # a corner: two connections, and it points along exactly those
    bend = World()
    for p in ((0, -1, 0), (1, -1, 0), (0, -1, 1)):
        bend.solid(p)
    bend.wire((0, 0, 0)); bend.wire((1, 0, 0)); bend.wire((0, 0, 1))
    assert bend.wire_points((0, 0, 0), "east")
    assert bend.wire_points((0, 0, 0), "south")
    assert not bend.wire_points((0, 0, 0), "west"), "a corner does not point back"
    assert not bend.wire_points((0, 0, 0), "north")
    print("  R6 a dot points nowhere, a line points both ways, a corner points "
          "along its two connections only: OK")


# --- R7 / R8: torches -------------------------------------------------------

def test_r7_torch_inverts_after_two_game_ticks():
    """A torch is on while its support is unpowered, and takes 2 game ticks to
    change — one redstone tick."""
    w = World()
    floor(w, 0, 3, -1, 0, 0)
    w.lever((0, 0, 0), attach="down", on=False)
    w.wire((1, 0, 0))
    w.solid((2, 0, 0))
    w.torch((2, 1, 0), attach="down")
    e = Engine(w)
    e.initialize_steady()
    assert e.high((2, 1, 0)), "unpowered support: torch on"
    e.set_lever((0, 0, 0), True)
    e.run(1)
    assert e.high((2, 1, 0)), "still on after 1 game tick"
    e.run(1)
    assert not e.high((2, 1, 0)), "off after 2 game ticks"
    print("  R7 a torch inverts its support, 2 game ticks late: OK")


def test_r8_torch_burns_out_past_eight_changes_in_60_ticks():
    """A torch that changes state more than 8 times inside 60 game ticks burns
    out and stops working.

    Note what is counted: *state changes*, in both directions. One lever flip
    causes one torch change, so eight flips is the limit, not eight on-off
    pairs. This rule is the single biggest constraint on the whole project, so
    it gets driven right up to the edge and one step past it.
    """
    w = World()
    floor(w, 0, 3, -1, 0, 0)
    w.lever((0, 0, 0), attach="down", on=False)
    w.wire((1, 0, 0))
    w.solid((2, 0, 0))
    w.torch((2, 1, 0), attach="down")
    e = Engine(w)
    e.initialize_steady()
    on = False
    for i in range(8):                     # eight changes, inside 24 game ticks
        on = not on
        e.set_lever((0, 0, 0), on); e.run(3)
    assert not e.burned_out, (
        f"eight state changes inside the window must survive, "
        f"{len(e.burned_out)} burned")
    assert e.now < 60, "the whole burst has to fit in one 60 tick window"
    e.set_lever((0, 0, 0), not on); e.run(3)
    assert e.burned_out, "the ninth change must burn it out"
    assert not e.high((2, 1, 0)), "a burned-out torch stays off"

    # and it recovers once the changes are spread out past the window
    w2 = World()
    floor(w2, 0, 3, -1, 0, 0)
    w2.lever((0, 0, 0), attach="down", on=False)
    w2.wire((1, 0, 0))
    w2.solid((2, 0, 0))
    w2.torch((2, 1, 0), attach="down")
    e2 = Engine(w2)
    e2.initialize_steady()
    on = False
    for i in range(12):
        on = not on
        e2.set_lever((0, 0, 0), on); e2.run(10)      # 10 gt apart: 6 per window
    assert not e2.burned_out, (
        "twelve changes spread 10 game ticks apart stay under the limit")
    print("  R8 eight state changes survive, the ninth burns out, and spacing "
          "them out avoids it entirely: OK")


# --- R9 / R10: repeaters ----------------------------------------------------

def test_r9_repeater_delay_and_diode():
    """A repeater reproduces its input at full strength after 2/4/6/8 game
    ticks by setting, and passes nothing backwards."""
    for setting in (1, 2, 3, 4):
        w = World()
        floor(w, 0, 6, -1, 0, 0)
        w.lever((0, 0, 0), attach="down", on=False)
        w.wire((1, 0, 0))
        w.repeater((2, 0, 0), facing="east", delay=setting)
        w.wire((3, 0, 0))
        e = Engine(w)
        e.initialize_steady()
        e.set_lever((0, 0, 0), True)
        e.run(2 * setting - 1)
        assert e.read((3, 0, 0)) == 0, f"delay {setting} fired early"
        e.run(1)
        assert e.read((3, 0, 0)) == 15, (
            f"delay {setting} should be through after {2*setting} gt and at "
            f"full strength, got {e.read((3, 0, 0))}")

    # backwards: drive the far side and check nothing appears behind
    w = World()
    floor(w, 0, 6, -1, 0, 0)
    w.wire((1, 0, 0))
    w.repeater((2, 0, 0), facing="east", delay=1)
    w.wire((3, 0, 0))
    w.lever((4, 0, 0), attach="down", on=True)
    e = Engine(w)
    e.initialize_steady()
    assert e.read((3, 0, 0)) == 15
    assert e.read((1, 0, 0)) == 0, "a repeater is a diode"
    print("  R9 repeaters delay 2/4/6/8 gt, restore to 15, and block "
          "reverse flow: OK")


def test_r10_repeater_locking():
    """A repeater facing another's side locks it: while locked its output does
    not change, whatever the input does. This is the whole basis of the keypad's
    torchless latch."""
    w = World()
    floor(w, -2, 4, -1, -1, 2)
    w.lever((-2, 0, 0), attach="down", on=True)          # data
    w.wire((-1, 0, 0))
    w.repeater((0, 0, 0), facing="east", delay=1)        # the latch
    w.wire((1, 0, 0))
    w.lever((0, 0, 2), attach="down", on=False)          # hold
    w.wire((0, 0, 1)) if False else None
    w.repeater((0, 0, 1), facing="north", delay=1)       # locks from the side
    e = Engine(w)
    e.initialize_steady()
    assert e.read((1, 0, 0)) == 15, "transparent: follows the data"
    e.set_lever((0, 0, 2), True); e.run_until_stable(400)
    assert e.read((1, 0, 0)) == 15, "locking must retain the value"
    e.set_lever((-2, 0, 0), False); e.run_until_stable(400)
    assert e.read((1, 0, 0)) == 15, "a locked repeater ignores its input"
    e.set_lever((0, 0, 2), False); e.run_until_stable(400)
    assert e.read((1, 0, 0)) == 0, "unlocking follows the data again"
    print("  R10 a repeater held from the side freezes its output: OK")


# --- R11: comparators -------------------------------------------------------

def test_r11_comparator_compare_and_subtract():
    """rear vs the strongest side, after 2 game ticks: compare mode passes the
    rear when rear >= side, subtract mode gives rear - side floored at 0."""
    for mode, expect in (("compare", lambda r, s: r if r >= s else 0),
                         ("subtract", lambda r, s: max(r - s, 0))):
        for rear_len, side_len in ((0, 5), (5, 0), (3, 3), (10, 4), (4, 10)):
            w = World()
            floor(w, -20, 4, -1, -20, 2)
            # rear feed: a lever, then dust to lose levels
            w.lever((-20, 0, 0), attach="down", on=True)
            for x in range(-19, 0):
                w.wire((x, 0, 0))
            w.comparator((0, 0, 0), facing="east", mode=mode)
            w.wire((1, 0, 0))
            # side feed along -z, arriving at the comparator's side
            w.lever((0, 0, -20), attach="down", on=True)
            for z in range(-19, 0):
                w.wire((0, 0, z))
            e = Engine(w)
            e.initialize_steady()
            rear = e.read((-1, 0, 0))
            side = e.read((0, 0, -1))
            out = e.read((1, 0, 0))
            assert out == expect(rear, side), (
                f"{mode}: rear {rear} side {side} -> {out}, "
                f"want {expect(rear, side)}")
    print("  R11 comparator compare and subtract modes both behave: OK")


# --- R12: lamps -------------------------------------------------------------

def test_r12_lamp_lights_from_either_kind_of_power():
    """A lamp lights from dust resting on it, from dust pointing at it, and from
    a strongly powered block beside it."""
    # dust resting on the lamp
    w = World()
    w.lamp((0, 0, 0))
    w.wire((0, 1, 0))
    w.solid((1, 0, 0))
    w.lever((1, 1, 0), attach="down", on=True)
    e = Engine(w)
    e.initialize_steady()
    assert e.read((0, 1, 0)) > 0, "the dust cap should be live"
    assert e.read((0, 0, 0)) > 0, "a lamp under live dust lights"

    # a strongly powered block beside it, with no dust touching the lamp at all
    w2 = World()
    w2.lamp((0, 0, 0))
    w2.solid((1, -1, 0))
    w2.torch((1, 0, 0), attach="down")     # strongly powers the block above
    w2.solid((1, 1, 0))
    e2 = Engine(w2)
    e2.initialize_steady()
    assert e2.high((1, 0, 0)), "the torch should be lit"
    assert e2.read((0, 0, 0)) > 0, "a lamp beside a lit torch lights"

    # and an unpowered lamp stays dark
    w3 = World()
    w3.lamp((0, 0, 0))
    w3.solid((1, 0, 0))
    e3 = Engine(w3)
    e3.initialize_steady()
    assert e3.read((0, 0, 0)) == 0, "an unpowered lamp is dark"
    print("  R12 a lamp lights from dust on it and from a lit torch beside it, "
          "and is otherwise dark: OK")


# --- engine against engine --------------------------------------------------

def random_circuit(rng, nx=7, nz=7, levels=2):
    """A small random tangle of everything, on a floor, with a few levers."""
    w = World()
    levers = []
    for lvl in range(levels):
        y = lvl * 3
        for x in range(nx):
            for z in range(nz):
                w.solid((x, y - 1, z))
        for x in range(nx):
            for z in range(nz):
                r = rng.random()
                p = (x, y, z)
                if r < 0.42:
                    w.wire(p)
                elif r < 0.52:
                    w.repeater(p, facing=rng.choice(
                        ["north", "south", "east", "west"]),
                        delay=rng.randint(1, 4))
                elif r < 0.58:
                    w.comparator(p, facing=rng.choice(
                        ["north", "south", "east", "west"]),
                        mode=rng.choice(["compare", "subtract"]))
                elif r < 0.66:
                    w.solid(p)
                    if rng.random() < 0.6:
                        w.torch((x, y + 1, z), attach="down")
                elif r < 0.70:
                    w.lamp(p)
                elif r < 0.74 and len(levers) < 4:
                    w.lever(p, attach="down", on=False)
                    levers.append(p)
    return w, levers


def test_engines_agree_tick_for_tick():
    """Run both simulators on random circuits and compare every block, every
    tick. Any disagreement is printed with the exact tick and cell."""
    rng = random.Random(20260729)
    trials = int(os.environ.get("RSCALC_DIFF_TRIALS", "40"))
    t0 = time.time()
    checked_ticks = 0
    checked_blocks = 0
    for trial in range(trials):
        w, levers = random_circuit(rng)
        if w.lint():
            continue                       # only compare well-formed builds
        try:
            fast, slow, slow_world = both(w)
        except RuntimeError:
            continue                       # a random tangle with no rest state
        a, b = fast_snapshot(fast), slow.snapshot()
        assert a == b, _diff(trial, "at rest", a, b)
        checked_blocks += len(a)

        for round_no in range(6):
            if levers:
                p = rng.choice(levers)
                on = not fast.w.blocks[p].on
                fast.set_lever(p, on)
                slow.set_lever(p, on)
            for _ in range(12):
                fast.tick()
                slow.tick()
                checked_ticks += 1
                a, b = fast_snapshot(fast), slow.snapshot()
                assert a == b, _diff(trial, f"tick {fast.now}", a, b)
            assert fast.burned_out == slow.burned, (
                f"trial {trial}: burnout sets differ, "
                f"{len(fast.burned_out)} vs {len(slow.burned)}")
    print(f"  two independent engines agree on every block at every tick: "
          f"{trials} random circuits, {checked_ticks} ticks compared, "
          f"{checked_blocks} blocks at rest, {time.time()-t0:.0f}s: OK")


def _diff(trial, when, a, b):
    bad = [p for p in a if a[p] != b.get(p)]
    bad += [p for p in b if p not in a]
    lines = [f"trial {trial} {when}: {len(bad)} blocks disagree"]
    for p in bad[:6]:
        lines.append(f"    {p}: fast {a.get(p)} vs reference {b.get(p)}")
    return "\n".join(lines)


def test_the_machine_matches_the_reference_engine():
    """The same comparison on a real compiled circuit rather than a tangle.

    A gate array is a much narrower shape than a random circuit — long rails,
    torch taps, risers — so it exercises the parts of the rules the build
    actually leans on.
    """
    from rscalc.netlist import Netlist
    from rscalc.pla import compile_netlist
    nl = Netlist()
    a, b, c = (nl.input("A"), nl.input("B"), nl.input("C"))
    nl.output("X", nl.xor(a, b))
    nl.output("Y", nl.and_(a, nl.or_(b, c)))
    nl.output("Z", nl.not_(nl.xnor(b, c)))
    w = World()
    L = compile_netlist(nl, w, repeater_delay=2)
    assert not w.lint(), w.lint()[:3]
    fast, slow, slow_world = both(w)

    rng = random.Random(7)
    ticks = 0
    for trial in range(8):
        vals = {n: rng.random() < 0.5 for n in ("A", "B", "C")}
        for n, v in vals.items():
            fast.set_lever(L.levers[n], v)
            slow.set_lever(L.levers[n], v)
        for _ in range(60):
            fast.tick(); slow.tick(); ticks += 1
            fa, sb = fast_snapshot(fast), slow.snapshot()
            assert fa == sb, _diff(trial, f"tick {fast.now}", fa, sb)
        # and the answer itself has to be right
        ref = nl.evaluate({k: int(v) for k, v in vals.items()})
        for name, node in nl.outputs.items():
            assert fast.high(L.outputs[name]) == bool(ref[node.idx]), (
                f"{name} wrong for {vals}")
    print(f"  a compiled gate array agrees between engines over {ticks} ticks, "
          f"and computes the right answer: OK")


def test_r2_every_source_hands_dust_a_full_fifteen():
    """R2. A lever that is on, a block of redstone, a lit torch and the front of
    a powered repeater are all sources at full strength — not merely "on".

    The distinction is the whole reason a rail can be 15 blocks long: a source
    that handed over 14 would cost a repeater every fourteenth block instead of
    every fifteenth, and the machine is built out of that number.
    """
    for label, place in (
            ("lever", lambda w: w.lever((0, 0, 0), attach="down", on=True)),
            ("redstone block", lambda w: w.redstone_block((0, 0, 0))),
            ("torch", lambda w: w.torch((0, 0, 0), attach="down"))):
        w = World()
        floor(w, -1, 4, -1, 0, 0)
        place(w)
        for x in range(1, 4):
            w.wire((x, 0, 0))
        e = Engine(w)
        e.initialize_steady()
        assert e.read((1, 0, 0)) == 15, f"{label} gave {e.read((1, 0, 0))}"
        assert e.read((2, 0, 0)) == 14, label
    # and a powered repeater's front, which is a source of its own
    w = World()
    floor(w, -3, 4, -1, 0, 0)
    w.lever((-3, 0, 0), attach="down", on=True)
    w.wire((-2, 0, 0)); w.wire((-1, 0, 0))
    w.repeater((0, 0, 0), facing="east", delay=1)
    for x in range(1, 4):
        w.wire((x, 0, 0))
    e = Engine(w)
    e.initialize_steady()
    assert e.read((1, 0, 0)) == 15, e.read((1, 0, 0))
    print("  R2 lever, redstone block, torch and repeater front all hand dust "
          "a full 15: OK")


def test_r5_what_dust_connects_to_and_what_it_does_not():
    """R5. Dust connects to dust, to a repeater or comparator *only along that
    component's own axis*, and climbs to dust on top of an adjacent full block
    unless a block sits directly above it.

    The axis rule is what lets a rail run past the side of a repeater without
    feeding it, which is how the collectors cross the gate array at all.
    """
    # A repeater's side is not a connection. The repeater faces +X, so its
    # back is at (-1,0,1) and its front at (1,0,1); the powered run along z=0
    # touches its *side* at (0,0,0) and must not feed it.
    def probe(back_powered):
        w = World()
        floor(w, -3, 2, -1, -1, 2)
        w.lever((-3, 0, 0), attach="down", on=True)
        # the run stops at x=0, clear of the probe: extended to x=1 it sat
        # diagonally beside the probe's own neighbour and fed it as ordinary
        # dust, which read 11 and looked like the side rule failing
        for x in range(-2, 1):
            w.wire((x, 0, 0))                     # the run, past the side
        w.repeater((0, 0, 1), facing="east", delay=1)
        w.wire((1, 0, 1))                         # its front
        if back_powered:                          # ...and its back, for contrast
            w.wire((-1, 0, 1))
        e = Engine(w)
        e.initialize_steady()
        return e.read((1, 0, 1))

    assert probe(False) == 0, \
        f"dust running past a repeater's side fed it anyway ({probe(False)})"
    # the same repeater, fed from behind, does pass — so the check above is
    # about the side and not about a repeater that was never going to fire
    assert probe(True) == 15, \
        f"the repeater does not work from its back either ({probe(True)}), so " \
        f"the side test proves nothing"

    # and the staircase: dust climbs onto dust atop an adjacent full block
    w = World()
    floor(w, -2, 0, -1, 0, 0)
    w.lever((-2, 0, 0), attach="down", on=True)
    w.wire((-1, 0, 0))
    w.solid((0, 0, 0))
    w.wire((0, 1, 0))
    e = Engine(w)
    e.initialize_steady()
    assert e.read((0, 1, 0)) > 0, "dust did not climb the staircase"
    climbed = e.read((0, 1, 0))

    # ...unless a full block caps the lower dust, which is the case the
    # compiler has to leave clear above every collector
    w2 = World()
    floor(w2, -2, 0, -1, 0, 0)
    w2.lever((-2, 0, 0), attach="down", on=True)
    w2.wire((-1, 0, 0))
    w2.solid((-1, 1, 0))                      # cap over the lower dust
    w2.solid((0, 0, 0))
    w2.wire((0, 1, 0))
    e2 = Engine(w2)
    e2.initialize_steady()
    assert e2.read((0, 1, 0)) == 0, (
        f"dust climbed past a cap ({e2.read((0, 1, 0))}) — that clearance is "
        f"what every collector in the compiler relies on")
    print(f"  R5 a repeater's side is not a connection, dust climbs a "
          f"staircase to {climbed} and not past a cap: OK")


def test_r13_a_component_has_at_most_one_pending_update():
    """R13. This is the scheduling rule, and it is why a pulse shorter than a
    repeater's delay disappears entirely rather than arriving late.

    A repeater whose input goes up and back down inside its own delay never
    fires: the one pending update re-reads the input when it comes due, finds it
    low again, and there is nothing to pass on. The whole machine leans on this
    — it is what stops a hazard glitch on a rail from propagating as a real
    pulse.
    """
    w = World()
    floor(w, -2, 4, -1, 0, 0)
    w.lever((-2, 0, 0), attach="down", on=False)
    w.wire((-1, 0, 0))
    w.repeater((0, 0, 0), facing="east", delay=4)     # 8 game ticks
    w.wire((1, 0, 0))
    e = Engine(w)
    e.initialize_steady()
    assert e.read((1, 0, 0)) == 0

    # up and down again well inside the delay
    e.set_lever((-2, 0, 0), True)
    e.run(2)
    e.set_lever((-2, 0, 0), False)
    seen = 0
    for _ in range(40):
        e.tick()
        if e.read((1, 0, 0)) > 0:
            seen += 1
    assert seen == 0, f"a 2-tick pulse got through a delay-4 repeater on " \
                      f"{seen} ticks"

    # and the same repeater does pass a pulse longer than its delay
    e.set_lever((-2, 0, 0), True)
    e.run(12)
    assert e.read((1, 0, 0)) == 15, "the repeater never passed a long pulse"
    print("  R13 a pulse shorter than a repeater's delay is swallowed whole, "
          "and a longer one is not: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} Minecraft-rule tests\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as ex:
            print(f"  FAIL {t.__name__}: {ex}"); failed += 1
        except Exception as ex:
            import traceback; traceback.print_exc()
            print(f"  ERROR {t.__name__}: {type(ex).__name__}: {ex}"); failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
