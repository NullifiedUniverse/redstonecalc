"""Exhaustively verify the NOR standard cell and the gates built from it."""

import sys, os, itertools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.cells import Placer, nor_cell


def drive(w, target, direction, length=3):
    """Run dust out of `target` in `direction` and cap it with a lever."""
    dx, dy, dz = {"east": (1, 0, 0), "west": (-1, 0, 0),
                  "north": (0, 0, -1), "south": (0, 0, 1)}[direction]
    p = target
    for _ in range(length):
        p = (p[0] + dx, p[1] + dy, p[2] + dz)
        w.solid((p[0], p[1] - 1, p[2]))
        w.wire(p)
    lev = (p[0] + dx, p[1] + dy, p[2] + dz)
    w.solid((lev[0], lev[1] - 1, lev[2]))
    w.lever(lev, attach="down", on=False)
    return lev


def build_nor(n_inputs):
    """A single NOR cell fed from up to three directions."""
    w = World()
    pl = Placer(w)
    cols, out = nor_cell(pl)
    dirs = ["east", "south", "north"][:n_inputs]
    levers = [drive(w, cols[0], d) for d in dirs]
    return w, levers, out


def test_nor_truth_tables():
    for n in (1, 2, 3):
        w, levers, out = build_nor(n)
        problems = w.lint()
        assert not problems, f"NOR{n} lint: {problems}"
        e = Engine(w)
        e.run_until_stable()
        for combo in itertools.product([False, True], repeat=n):
            for lev, v in zip(levers, combo):
                e.set_lever(lev, v)
            e.run_until_stable()
            expect = not any(combo)
            got = e.high(out)
            assert got == expect, f"NOR{n}{combo}: got {got} want {expect}"
        print(f"  NOR{n} truth table ({2**n} cases): OK")


def test_nor_latency():
    w, levers, out = build_nor(1)
    e = Engine(w)
    e.run_until_stable()
    assert e.high(out)
    e.set_lever(levers[0], True)
    n = 0
    while e.high(out):
        e.tick(); n += 1
        assert n < 20
    assert n == 2, f"NOR latency should be 2 game ticks, got {n}"
    print("  NOR cell latency = 2 game ticks (1 redstone tick): OK")


def build_chain(k):
    """k NOR cells in series; each cell's output feeds the next collector."""
    w = World()
    pl = Placer(w)
    outs = []
    prev_out = None
    for i in range(k):
        cell = Placer(w, origin=(-3 * i, 0, 0))
        cols, out = nor_cell(cell)
        if prev_out is not None:
            # previous output sits exactly where this collector is
            assert prev_out == cols[0], (prev_out, cols[0])
        prev_out = out
        outs.append(out)
    lever = drive(w, (0, 0, 0), "east")
    return w, lever, outs


def test_chain_inverts_and_accumulates_delay():
    w, lever, outs = build_chain(4)
    assert not w.lint(), w.lint()
    e = Engine(w)
    e.run_until_stable()
    for val in (True, False, True):
        e.set_lever(lever, val)
        e.run_until_stable()
        for i, o in enumerate(outs):
            expect = (not val) if i % 2 == 0 else val
            assert e.high(o) == expect, f"stage {i} with in={val}"
    print("  4-cell chain alternates polarity: OK")

    # measure end-to-end delay of the 4-stage chain
    e.set_lever(lever, False)
    e.run_until_stable()
    e.set_lever(lever, True)
    n = 0
    target = outs[-1]
    want = True          # stage 3 (odd) follows the input
    while e.high(target) != want:
        e.tick(); n += 1
        assert n < 40
    assert n == 8, f"4 cells should take 8 game ticks, got {n}"
    print("  4-cell chain latency = 8 game ticks (1 rs tick/cell): OK")


def build_gate(kind):
    """AND / OR / XOR built from NOR cells, laid out on one plane."""
    w = World()
    # inputs A and B enter as two long rails we can tap
    # rail A at z=-6, rail B at z=6, both running along +x
    for z in (-6, 6):
        for x in range(-14, 3):
            w.solid((x, -1, z))
            w.wire((x, 0, z))
    la = (3, 0, -6); lb = (3, 0, 6)
    w.solid((3, -1, -6)); w.lever(la, attach="down", on=False)
    w.solid((3, -1, 6)); w.lever(lb, attach="down", on=False)

    def tap(from_z, to_z, at_x):
        """Branch off a rail toward the middle, ending 1 short of `to_z`."""
        stepz = 1 if to_z > from_z else -1
        z = from_z
        while z != to_z:
            z += stepz
            w.solid((at_x, -1, z))
            w.wire((at_x, 0, z))
        return (at_x, 0, to_z)

    if kind == "OR":
        # OR(A,B) = NOT(NOR(A,B))
        c1 = Placer(w, origin=(0, 0, 0))
        cols, n1 = nor_cell(c1)
        tap(-6, -1, 0); tap(6, 1, 0)
        c2 = Placer(w, origin=(-3, 0, 0))
        cols2, out = nor_cell(c2)
        return w, la, lb, out, lambda a, b: a or b
    if kind == "AND":
        # AND(A,B) = NOR(NOT A, NOT B)
        na = Placer(w, origin=(0, 0, -3))
        _, nao = nor_cell(na)
        tap(-6, -3, 0)
        nb = Placer(w, origin=(0, 0, 3))
        _, nbo = nor_cell(nb)
        tap(6, 3, 0)
        # bring both inverted signals into a third cell at x=-7
        c = Placer(w, origin=(-7, 0, 0))
        cols, out = nor_cell(c)
        # nao is at (-3,0,-3); route to (-7,0,-1) then into collector
        for x in range(-7, -3):
            w.solid((x, -1, -3)); w.wire((x, 0, -3))
        for z in (-2, -1):
            w.solid((-7, -1, z)); w.wire((-7, 0, z))
        for x in range(-7, -3):
            w.solid((x, -1, 3)); w.wire((x, 0, 3))
        for z in (2, 1):
            w.solid((-7, -1, z)); w.wire((-7, 0, z))
        return w, la, lb, out, lambda a, b: a and b
    raise ValueError(kind)


def test_or_gate():
    w, la, lb, out, fn = build_gate("OR")
    assert not w.lint(), w.lint()
    e = Engine(w); e.run_until_stable()
    for a, b in itertools.product([False, True], repeat=2):
        e.set_lever(la, a); e.set_lever(lb, b); e.run_until_stable()
        assert e.high(out) == fn(a, b), f"OR({a},{b}) = {e.high(out)}"
    print("  OR gate (2 cells): OK")


def test_and_gate():
    w, la, lb, out, fn = build_gate("AND")
    assert not w.lint(), w.lint()
    e = Engine(w); e.run_until_stable()
    for a, b in itertools.product([False, True], repeat=2):
        e.set_lever(la, a); e.set_lever(lb, b); e.run_until_stable()
        assert e.high(out) == fn(a, b), f"AND({a},{b}) = {e.high(out)}"
    print("  AND gate (3 cells): OK")


def test_a_long_collector_is_one_wired_or():
    """`collector_len` is the feature everything else is built on, and it was
    the one nothing exercised.

    A NOR cell's collector is a wired-OR: extend it along Z and any number of
    dust lines can merge into one gate, which is how every PLA gate in the
    machine gathers its inputs. Every test above used the default length of
    one, so no cell-local coordinate was ever anything but z=0 — and
    `tools/mutate_core.py` proved what that costs by mirroring the Placer's
    frame along Z and watching the whole suite stay green.

    Two claims, then: the cells land where the frame says they do, offset and
    all, and a torch really does NOR however many lines reach its base.
    """
    # the frame, at an origin that is not the origin
    probe = World()
    cols = nor_cell(Placer(probe, origin=(5, 1, 7)), collector_len=5)[0]
    assert cols == [(5, 1, 7 + z) for z in (-2, -1, 0, 1, 2)], cols

    # and the behaviour, driven from three points along that collector
    w = World()
    cols, out = nor_cell(Placer(w), collector_len=5)
    # every other cell, so the three feed rails do not touch each other
    levers = [drive(w, cols[i], "east") for i in (0, 2, 4)]
    assert not w.lint(), w.lint()[:3]
    e = Engine(w)
    e.run_until_stable()
    for combo in itertools.product([False, True], repeat=3):
        for lev, v in zip(levers, combo):
            e.set_lever(lev, v)
        e.run_until_stable()
        assert e.high(out) == (not any(combo)), f"NOR{combo} along a collector"
    print("  a 5-cell collector places where the frame says and NORs all "
          f"{2**3} ways: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} cell tests\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as ex:
            print(f"  FAIL {t.__name__}: {ex}"); failed += 1
        except Exception as ex:
            print(f"  ERROR {t.__name__}: {type(ex).__name__}: {ex}"); failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
