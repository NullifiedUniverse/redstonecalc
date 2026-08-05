"""The Mk II console's logic, without placing a block.

`tests/test_console.py` drives the console through real keypads in the
tick-accurate engine, which is the measurement that matters and is exactly why
it is held back to `--slow`. The consequence was that the fast suite knew
nothing about `rscalc/console.py` at all, and `tools/mutate_core.py` proved it:
building the decimal adder's *propagate* term as an AND instead of an OR — which
is the classic carry-lookahead mistake, and wrong on every carry — left the fast
suite entirely green.

This is the cheap half, the same split `tests/test_alu_logic.py` uses: evaluate
the netlist directly over every pair of keys a player can press. A hundred
vectors, checked at the far end — the fourteen segment lines that drive the
lamps, not the adder's internal carries — so it verifies what the display will
actually show rather than what the arithmetic was meant to be.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.console import build_netlist
from rscalc.display import DIGIT_SEGMENTS, SEGS

#: the tens digit has no decoder — it is blank or a 1, drawn with two segments
TENS_SEGS = ("b", "c")


def read(nl, v):
    """What the lamps would show: (units segments, tens segments, ready)."""
    units = "".join(s for s in SEGS if v[nl.outputs[f"SEG0_{s}"].idx])
    tens = "".join(s for s in TENS_SEGS if v[nl.outputs[f"SEG1_{s}"].idx])
    return units, tens, bool(v[nl.outputs["READY"].idx])


def press(nl, a, b):
    vals = {f"KA{k}": k == a for k in range(10)}
    vals.update({f"KB{k}": k == b for k in range(10)})
    vals.update({f"GAP{i}": False for i in range(3)})
    return read(nl, nl.evaluate(vals))


def test_every_pair_of_keys():
    """All 100 sums, read off the segment lines.

    A one-digit decimal adder is a binary add and then +6 whenever the result
    passes nine, and the +6 is where this goes wrong quietly: 7+8 and 9+9 come
    out as plausible numerals rather than as nothing at all.
    """
    nl = build_netlist()
    bad = []
    for a, b in itertools.product(range(10), repeat=2):
        units, tens, ready = press(nl, a, b)
        total = a + b
        want_u = DIGIT_SEGMENTS[total % 10]
        want_t = "bc" if total >= 10 else ""
        if sorted(units) != sorted(want_u) or tens != want_t:
            bad.append(f"{a}+{b}={total}: units {units!r} tens {tens!r}, "
                       f"wanted {want_u!r} {want_t!r}")
        if not ready:
            bad.append(f"{a}+{b}: READY is dark with both keys down")
    assert not bad, f"{len(bad)} of 100 wrong, e.g. {bad[:3]}"
    print(f"  100 key pairs, every segment line and the carry into the tens "
          f"digit: OK")


def test_the_tens_digit_is_blank_rather_than_a_leading_zero():
    """What a calculator does. It is also the only reason the tens digit needs
    no decoder — blank or a 1, and nothing else can reach it."""
    nl = build_netlist()
    for a, b in itertools.product(range(10), repeat=2):
        _, tens, _ = press(nl, a, b)
        assert tens in ("", "bc"), f"{a}+{b}: tens digit shows {tens!r}"
        assert (tens == "bc") == (a + b >= 10), f"{a}+{b}: tens {tens!r}"
    print("  the tens digit is blank under ten and a 1 at or above it, never "
          "a zero: OK")


def test_ready_needs_a_key_on_both_pads():
    """Key 0 encodes to all bits low, so on its own it drives nothing and would
    be optimised out of the netlist — yet its latch has to exist, because
    pressing it is what clears the other nine. READY is what keeps it."""
    nl = build_netlist()
    none = {f"KA{k}": False for k in range(10)}
    none.update({f"KB{k}": False for k in range(10)})
    none.update({f"GAP{i}": False for i in range(3)})

    assert not read(nl, nl.evaluate(none))[2], "READY is lit with nothing down"
    only_a = dict(none, KA0=True)
    assert not read(nl, nl.evaluate(only_a))[2], "READY is lit with only A"
    only_b = dict(none, KB0=True)
    assert not read(nl, nl.evaluate(only_b))[2], "READY is lit with only B"
    both = dict(none, KA0=True, KB0=True)
    assert read(nl, nl.evaluate(both))[2], \
        "READY is dark with 0 pressed on both pads — the key that encodes to " \
        "nothing is exactly the one this output exists to keep"
    # and every key of both pads is still a node in the netlist
    missing = [n for n in ([f"KA{k}" for k in range(10)]
                           + [f"KB{k}" for k in range(10)])
               if n not in nl.inputs]
    assert not missing, f"keys optimised out of the netlist: {missing}"
    print("  READY needs both pads, and all 20 keys survive into the netlist "
          "including the two that encode to nothing: OK")


def test_the_spacer_rails_are_never_driven():
    """Three undriven rails keep the two keypads' `any key is down` buses from
    touching. If a GAP input ever changed an output, the bands would not be
    separated at all — the netlist would just be quietly relying on them."""
    nl = build_netlist()
    base = {f"KA{k}": k == 3 for k in range(10)}
    base.update({f"KB{k}": k == 4 for k in range(10)})
    base.update({f"GAP{i}": False for i in range(3)})
    was = read(nl, nl.evaluate(base))
    for i in range(3):
        got = read(nl, nl.evaluate(dict(base, **{f"GAP{i}": True})))
        assert got[:2] == was[:2], \
            f"driving GAP{i} changed the display from {was[:2]} to {got[:2]}"
    print("  the three spacer rails move nothing on the display: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} console logic tests\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as ex:
            print(f"  FAIL {t.__name__}: {ex}")
            failed += 1
        except Exception:
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
