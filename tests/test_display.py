"""Verify the Mk II display and memory modules on placed blocks."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.display import (build_digit, lit_segments, DIGIT_SEGMENTS, SEGS)


def test_seven_segment_digit():
    """Every numeral 0-9 renders, and nothing renders when blank."""
    w, levers, lamps = build_digit()
    problems = w.lint()
    assert not problems, problems[:4]
    e = Engine(w)
    e.initialize_steady()
    worst = 0
    for digit in range(10):
        want = DIGIT_SEGMENTS[digit]
        for s in SEGS:
            e.set_lever(levers[s], s in want)
        worst = max(worst, e.run_until_stable(4000))
        got = lit_segments(e, lamps)
        assert got == want, f"digit {digit}: lamps show {got!r}, want {want!r}"
    for s in SEGS:
        e.set_lever(levers[s], False)
    e.run_until_stable(4000)
    assert lit_segments(e, lamps) == "", "display should go blank"
    assert not e.burned_out
    (x0, y0, z0), (x1, y1, z1) = w.bounds()
    print(f"  seven-segment digit: 0-9 all render, blank works, "
          f"{len(w.blocks)} blocks, {x1-x0+1}x{y1-y0+1}x{z1-z0+1}, "
          f"{worst} gt: OK")


def build_latch():
    """A D-latch made from a locked repeater — no torches anywhere."""
    w = World()
    for x in range(-3, 4):
        for z in range(-1, 4):
            w.solid((x, -1, z))
    w.lever((-3, 0, 0), attach="down", on=False)          # DATA
    w.wire((-2, 0, 0)); w.wire((-1, 0, 0))
    w.repeater((0, 0, 0), facing="east", delay=1)         # the latch itself
    w.wire((1, 0, 0)); w.wire((2, 0, 0))
    w.lever((0, 0, 3), attach="down", on=False)           # HOLD
    w.wire((0, 0, 2))
    w.repeater((0, 0, 1), facing="north", delay=1)        # locks it from the side
    return w, (-3, 0, 0), (0, 0, 3), (2, 0, 0)


def test_repeater_lock_latch():
    """Memory with no torches, so torch burnout cannot happen at all.

    The lock has to be asserted a tick *before* the data changes; assert both
    in the same tick and the data wins the race, storing the new value rather
    than holding the old one.
    """
    w, D, H, Q = build_latch()
    assert w.count("redstone_torch") == 0, "a latch with torches can burn out"
    e = Engine(w)
    e.initialize_steady()

    def data(v):
        e.set_lever(D, bool(v)); e.run_until_stable(2000)

    def hold(v):
        e.set_lever(H, bool(v)); e.run_until_stable(2000)

    data(1); assert e.high(Q), "transparent: should follow data"
    hold(1); assert e.high(Q), "locking must retain the value"
    data(0); assert e.high(Q), "locked latch must ignore data"
    data(1); data(0)
    assert e.high(Q), "locked latch must ignore repeated changes"
    hold(0); assert not e.high(Q), "releasing should follow data again"
    data(1); assert e.high(Q)
    hold(1); data(0); assert e.high(Q), "should still hold"

    # the race: changing both together lets the data through
    w2, D2, H2, Q2 = build_latch()
    e2 = Engine(w2); e2.initialize_steady()
    e2.set_lever(D2, True); e2.run_until_stable(2000)
    e2.set_lever(H2, True); e2.set_lever(D2, False)   # same tick, no settle between
    e2.run_until_stable(2000)
    assert not e2.high(Q2), "expected the documented race when lock and data move together"

    assert not e.burned_out
    print(f"  repeater-lock D-latch: holds correctly, {len(w.blocks)} blocks, "
          f"0 torches so burnout is impossible; lock-before-data race "
          f"reproduced: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} display/memory tests\n")
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
