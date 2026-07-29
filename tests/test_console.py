"""The whole calculator, driven the way a player drives it.

Nothing here reaches inside the machine: keys are pressed and released as
buttons, and the answer is read off the lamps.
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import Engine
from rscalc.console import build_console, read_display
from rscalc.keypad import press
from rscalc.display import lit_segments

DELAY = 2          # large builds need delay-2 repeaters to dodge burnout


def test_console_all_sums():
    """Every A+B for single decimal digits: 100 combinations."""
    nl, w, L, pads, lamps, stats, ready = build_console(repeater_delay=DELAY)
    problems = w.lint()
    assert not problems, problems[:4]
    e = Engine(w)
    e.initialize_steady()
    t0 = time.time()
    bad = 0
    for a in range(10):
        for b in range(10):
            press(e, pads["A"], a, hold_ticks=20 * DELAY)
            press(e, pads["B"], b, hold_ticks=20 * DELAY)
            e.run_until_stable(80000)
            got = read_display(e, lamps)
            if got != a + b:
                bad += 1
                if bad <= 3:
                    print(f"    {a}+{b} showed {got}, lamps "
                          f"{lit_segments(e, lamps['1'])}|"
                          f"{lit_segments(e, lamps['0'])}")
            assert e.read(ready) > 0, "ready lamp should be lit once both are in"
    assert not e.burned_out, f"{len(e.burned_out)} torches burned out"
    assert bad == 0, f"{bad}/100 wrong"
    (x0, y0, z0), (x1, y1, z1) = w.bounds()
    print(f"  console: all 100 sums correct, read off the lamps. "
          f"{len(w.blocks)} blocks, {x1-x0+1}x{y1-y0+1}x{z1-z0+1}, "
          f"{nl.gate_count()} gates, depth {nl.depth()}, "
          f"loom {stats['nets']} nets / {stats['towers']} towers, "
          f"{time.time()-t0:.0f}s: OK")


def test_console_holds_between_entries():
    """Operand A must survive while B is typed, and survive being retyped."""
    nl, w, L, pads, lamps, stats, ready = build_console(repeater_delay=DELAY)
    e = Engine(w)
    e.initialize_steady()
    press(e, pads["A"], 7, hold_ticks=20 * DELAY)
    for b in (1, 2, 3, 9):
        press(e, pads["B"], b, hold_ticks=20 * DELAY)
        e.run_until_stable(80000)
        assert read_display(e, lamps) == 7 + b, f"A should still be 7 for B={b}"
    # now retype A without touching B
    press(e, pads["A"], 2, hold_ticks=20 * DELAY)
    e.run_until_stable(80000)
    assert read_display(e, lamps) == 2 + 9, "B should still be 9"
    assert not e.burned_out
    print("  console: operands latch and survive each other's entry: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} console tests\n")
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
