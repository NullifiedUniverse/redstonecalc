"""Error handling and structural invariants: the things that fail *silently*.

Every bug that cost real time on this project had the same shape — the build
looked healthy and computed nonsense. So these tests check the invariants the
rest of the design leans on, and check that the failure modes are loud:

  * the wiring loom never routes two nets through the same cell
  * a gate cannot tap one rail through both polarities
  * constants fold instead of producing unbuildable gates
  * lint catches unsupported dust and torches
  * a cached steady state that does not belong to a world is refused
  * a keypad wider than the shared bus can carry is refused
"""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine, Block
from rscalc.netlist import Netlist
from rscalc.logic import sop, fold_term, any_of
from rscalc.display import seven_seg, SEGS
from rscalc import steady


# --- the loom's central claim -----------------------------------------------

class WriteSpy:
    """Records which phase of the build wrote each cell."""

    def __init__(self):
        self.owner = {}
        self.phase = "start"
        self.clashes = []
        self._orig = World.set

    def __enter__(self):
        spy = self

        def patched(world, pos, block):
            prev = spy.owner.get(pos)
            if prev is not None and prev != spy.phase:
                spy.clashes.append((prev, spy.phase, pos))
            spy.owner[pos] = spy.phase
            return spy._orig(world, pos, block)

        World.set = patched
        return self

    def __exit__(self, *a):
        World.set = self._orig


def test_loom_routes_never_cross():
    """No two loom nets may write the same cell — that is the whole design.

    Each net climbs at its own turn column from the shared level to its panel's
    feed level, and each net's final run travels east past every later turn
    column. Hand out the columns in the wrong order and a run crosses a taller
    tower sharing its Z, which silently merges two segment lines.
    """
    import rscalc.machine as M
    import rscalc.display as D

    orig_digit, orig_row, orig_route = M.build_digit, M.build_lamp_row, M.route
    with WriteSpy() as spy:
        def digit(*a, **k):
            spy.phase = "panel"
            return orig_digit(*a, **k)

        def row(*a, **k):
            spy.phase = "panel"
            return orig_row(*a, **k)

        def route(w, nets, ya, yb, y_disp, turn_x0):
            spy.phase = f"net@{turn_x0}"
            return orig_route(w, nets, ya, yb, y_disp, turn_x0)

        M.build_digit, M.build_lamp_row, M.route = digit, row, route
        spy.phase = "pla"
        try:
            m = M.build_machine(repeater_delay=2)
        finally:
            M.build_digit, M.build_lamp_row, M.route = (
                orig_digit, orig_row, orig_route)

    loom_on_loom = [c for c in spy.clashes
                    if c[0].startswith("net") and c[1].startswith("net")]
    assert not loom_on_loom, (
        f"{len(loom_on_loom)} cells written by two different loom nets, "
        f"first {loom_on_loom[:3]}")

    # The one overwrite that is meant to happen: the last cell of a net's run
    # replaces the panel's feed-lane entry, which is how the signal is handed
    # over. Two cells per net — the dust and the floor beneath it.
    handover = [c for c in spy.clashes if c[0] == "panel"]
    n_nets = m.stats["nets"]
    assert len(handover) == 2 * n_nets, (
        f"expected {2 * n_nets} hand-over cells, got {len(handover)}")
    assert len(spy.clashes) == len(handover), "unexplained overwrites"
    assert not m.world.lint(), m.world.lint()[:3]
    print(f"  loom: {n_nets} nets, no shared cells, "
          f"{len(handover)} hand-over cells as designed: OK")


# --- the netlist refuses what it cannot build --------------------------------

def test_gate_rejects_both_polarities():
    nl = Netlist()
    a = nl.input("A")
    try:
        nl.gate([(a, True), (a, False)])
    except ValueError as ex:
        assert "both ways" in str(ex), ex
    else:
        raise AssertionError("tapping a rail both ways must be refused")
    # the same rail twice with the same polarity is harmless and folds away
    g = nl.gate([(a, True), (a, True)])
    assert len(g.taps) == 1, g.taps
    print("  netlist: a rail tapped both ways is refused, a repeat folds: OK")


def test_constants_fold():
    nl = Netlist()
    a = nl.input("A")
    z, one = nl.zero(), nl.one()
    assert fold_term(nl, [(z, True), (a, True)]) == 0, "0 AND x is false"
    assert fold_term(nl, [(z, False), (a, True)]) == [(a, True)], "NOT 0 drops out"
    assert fold_term(nl, [(z, False)]) == 1, "a term of only true literals"
    assert sop(nl, [[(z, True)]]) is nl.zero(), "an impossible sum is 0"
    assert sop(nl, [[(z, False)], [(a, True)]]) is nl.one(), "a true term wins"
    assert any_of(nl, [z, z]) is nl.zero()
    assert any_of(nl, [z, one]) is nl.one()
    # a BCD digit whose upper bits are the constant rail must still decode
    d = [a, z, z, z]
    segs = seven_seg(nl, d)
    assert set(segs) == set(SEGS)
    v = nl.evaluate({"A": 1})
    on = "".join(s for s in SEGS if v[segs[s].idx])
    assert on == "bc", f"digit 1 should light b and c, got {on!r}"
    v = nl.evaluate({"A": 0})
    on = "".join(s for s in SEGS if v[segs[s].idx])
    assert on == "abcdef", f"digit 0 should light abcdef, got {on!r}"
    print("  logic: constants fold, and a part-constant digit still decodes: OK")


# --- lint catches what the simulator will not model faithfully ---------------

def test_lint_catches_unsupported_and_illegal():
    w = World()
    w.wire((0, 0, 0))                      # dust on nothing
    assert any("unsupported" in p for p in w.lint()), w.lint()

    w = World()
    w.torch((0, 0, 0), attach="down")      # torch with no support
    assert any("no conductive support" in p for p in w.lint()), w.lint()

    w = World()
    w.solid((0, 1, 0))
    w.torch((0, 0, 0), attach="up")        # attached to a block's underside
    assert any("illegal" in p for p in w.lint()), w.lint()

    w = World()
    w.solid((0, -1, 0))
    w.wire((0, 0, 0))
    assert not w.lint(), w.lint()
    print("  lint: unsupported dust, unsupported torches and ceiling torches "
          "are all flagged: OK")


# --- the steady-state cache refuses to be wrong ------------------------------

def _tiny_world():
    w = World()
    for x in range(4):
        w.solid((x, -1, 0))
    w.lever((0, 0, 0), attach="down", on=False)
    w.wire((1, 0, 0))
    w.solid((2, 0, 0))
    w.torch((2, 1, 0), attach="down")
    return w


def test_steady_cache_is_keyed_to_the_world():
    a = _tiny_world()
    ea = Engine(a)
    ea.initialize_steady()
    digest = steady.world_digest(a)

    b = _tiny_world()
    b.repeater((3, 0, 0), facing="east", delay=2)   # a different world
    assert steady.world_digest(b) != digest, "the digest must notice a change"

    old = steady.CACHE_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            steady.CACHE_DIR = d
            steady.save(a)
            assert steady.load(_tiny_world()), "the same world must load"
            assert not steady.load(b), "a different world must not load"
    finally:
        steady.CACHE_DIR = old
    print("  steady cache: keyed to the placed blocks, a changed world "
          "misses rather than loading a stale state: OK")


def test_adopt_state_detects_a_wrong_resting_state():
    w = _tiny_world()
    e = Engine(w)
    e.initialize_steady()
    e.adopt_state(check=True)              # the real state verifies
    w.blocks[(2, 1, 0)].lit = not w.blocks[(2, 1, 0)].lit   # corrupt it
    try:
        Engine(w, settle_on_init=False).adopt_state(check=True)
    except RuntimeError as ex:
        assert "fixed point" in str(ex), ex
    else:
        raise AssertionError("a corrupted resting state must be refused")
    print("  steady cache: a state that is not a fixed point is refused: OK")


# --- components refuse what they cannot do -----------------------------------

def test_keypad_refuses_more_keys_than_its_bus_carries():
    from rscalc.keypad import build_keypad
    w = World()
    targets = [(0, 0, 4 * i) for i in range(16)]
    try:
        build_keypad(w, targets)
    except AssertionError as ex:
        assert "verified" in str(ex), ex
    else:
        raise AssertionError("a 16-key pad must be refused, not built broken")
    print("  keypad: a pad wider than the bus is verified for is refused: OK")


def test_export_refuses_unknown_blocks():
    from rscalc import mcbuild
    try:
        mcbuild.block_state(Block("observer"))
    except ValueError:
        pass
    else:
        raise AssertionError("an unexportable block must fail loudly")
    print("  build output: a block with no Minecraft mapping fails loudly: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} error-handling tests\n")
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
