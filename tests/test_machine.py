"""The Mk III 10-bit machine, driven the way a player drives it.

Nothing here reaches inside: operand bits are levers that get flipped, the
operation is a button that gets pressed and released, and the answer is read off
the lamps and the flag row.

The first run builds the machine's resting state, which takes a few minutes on
half a million blocks; it is cached afterwards, so later runs start in seconds.
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import Engine
from rscalc.machine import build_machine, FLAGS
from rscalc.alu import OPS
from rscalc.steady import settled_engine
from rscalc import steady

from rscalc.machine import DEFAULT_DELAY
DELAY = int(os.environ.get("RSCALC_DELAY", str(DEFAULT_DELAY)))
_CACHE = {}


def machine(reset=True):
    """Built once per process — standing it up is the expensive part.

    Each test starts from the resting state again, so one failure cannot
    cascade into the next and make four tests look broken when one is.
    """
    if DELAY not in _CACHE:
        m = build_machine(repeater_delay=DELAY)
        e = settled_engine(m.world, Engine, verify=True, quiet=True)
        _CACHE[DELAY] = (m, e)
        return _CACHE[DELAY]
    m, e = _CACHE[DELAY]
    if reset:
        assert steady.load(m.world), "the resting state should be cached by now"
        e.adopt_state(check=False)
    return m, e


def do(m, e, op, a, b):
    """Set the operands, press the operation, wait for it to settle.

    Returns the game ticks from the keypress to the answer appearing — which
    is the number a player experiences.
    """
    t0 = e.now
    m.set_operands(e, a, b)
    m.press_op(e, op)
    e.run_until_stable(400000)
    return e.now - t0


def check(m, e, op, a, b):
    want, wflags = m.expect(op, a, b)
    got, flags = m.read_value(e), m.read_flags(e)
    assert got == want, (f"{OPS[op]} {a},{b}: lamps show {got}, want {want}")
    assert flags == wflags, (f"{OPS[op]} {a},{b}: flags {flags}, want {wflags}")


def test_structure():
    """The placed machine has to be well formed before anything else matters."""
    m, e = machine()
    problems = m.world.lint()
    assert not problems, problems[:4]
    (x0, y0, z0), (x1, y1, z1) = m.world.bounds()
    assert m.stats["nets"] == 7 * m.ndigits + len(FLAGS)
    assert len(m.levers) == 2 * m.width
    assert len(m.keys) == len(OPS)
    print(f"  structure: {m.stats['blocks']:,} blocks, "
          f"{x1-x0+1}x{y1-y0+1}x{z1-z0+1}, {m.stats['gates']} gates, "
          f"depth {m.stats['depth']}, loom {m.stats['nets']} nets / "
          f"{m.stats['towers']} towers, lint clean: OK")


def test_four_basic_operations():
    """ADD, SUB, AND and OR over the corners and a spread of values."""
    m, e = machine()
    basics = [OPS.index(o) for o in ("ADD", "SUB", "AND", "OR")]
    cases = []
    for op in basics:
        cases += [(op, 0, 0), (op, 1023, 1023), (op, 1023, 1), (op, 512, 512),
                  (op, 999, 24), (op, 7, 5), (op, 1000, 1), (op, 341, 682)]
    t0, worst = time.time(), 0
    for op, a, b in cases:
        worst = max(worst, do(m, e, op, a, b))
        check(m, e, op, a, b)
    assert not e.burned_out, f"{len(e.burned_out)} torches burned out"
    print(f"  four basic operations: {len(cases)} cases correct on the lamps, "
          f"worst settle {worst} gt ({worst/20:.0f} s in game), "
          f"{time.time()-t0:.0f}s: OK")


def test_all_eight_operations():
    """Every operation the ALU has, including the flags."""
    m, e = machine()
    cases = []
    for op in range(len(OPS)):
        cases += [(op, 0, 0), (op, 1023, 1023), (op, 682, 341), (op, 100, 900)]
    worst = 0
    for op, a, b in cases:
        worst = max(worst, do(m, e, op, a, b))
        check(m, e, op, a, b)
    assert not e.burned_out, f"{len(e.burned_out)} torches burned out"
    print(f"  all eight operations: {len(cases)} cases correct, "
          f"worst settle {worst} gt: OK")


def test_consecutive_without_reset():
    """A run of operations back to back, nothing cleared in between.

    This is the case a one-shot test misses: latches keep their previous value,
    only the levers that differ are moved, and every hazard in the machine fires
    on top of a state left by the last answer.
    """
    m, e = machine()
    script = [
        (0, 12, 34), (0, 12, 34),        # same sum twice: must not drift
        (1, 12, 34),                     # change the operation only
        (1, 900, 899),                   # change the operands only
        (0, 900, 899),
        (2, 900, 899), (3, 900, 899), (4, 900, 899),
        (0, 1023, 1023), (1, 0, 1023), (5, 0, 1023),
        (6, 511, 0), (7, 511, 0),
        (0, 0, 0), (0, 1023, 1),
    ]
    worst = 0
    for op, a, b in script:
        worst = max(worst, do(m, e, op, a, b))
        check(m, e, op, a, b)
    assert not e.burned_out, f"{len(e.burned_out)} torches burned out"
    print(f"  consecutive: {len(script)} operations back to back with no reset, "
          f"all correct, worst settle {worst} gt: OK")


def test_operands_hold_while_the_operation_changes():
    """The operand levers stay put; only the one-hot keypad moves."""
    m, e = machine()
    do(m, e, 0, 731, 292)
    check(m, e, 0, 731, 292)
    for op in (1, 2, 3, 4, 0):
        m.press_op(e, op)
        e.run_until_stable(400000)
        check(m, e, op, 731, 292)
    held = [k for k in range(len(OPS))
            if e.read(m.layout.levers[f"K{k}"]) > 0]
    assert held == [0], f"the keypad must stay one-hot, holds {held}"
    print("  operands hold across operation changes, keypad stays one-hot: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} machine tests at repeater delay {DELAY}\n")
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
