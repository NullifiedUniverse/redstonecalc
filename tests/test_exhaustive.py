"""The 10-bit logic, over every operand pair it can ever be given.

The placed machine is verified on blocks and ticks, which is the measurement
that matters — but a settle costs about a second, so that evidence is 300
vectors wide (`tools/verify_full.py`). Everything above 4 bits was therefore
sampled: `test_alu.py` is exhaustive at 4, boundaries plus a random spread at 8,
and the shipped width of 10 had never been swept at all.

It can be. A gate here is an OR of literals, so a thousand vectors cost the same
instruction as one if they are packed into the bits of an integer — see
`tools/verify_logic.py`. All eight operations across all 1,048,576 pairs, read
at the seven-segment lines and the flags, take half a minute.

This is the *logic*, not the redstone: nothing is placed and no tick is
simulated. It closes the gap between "the design is right" and "the design is
right at the width it ships at", which sampling could only ever make likely.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.alu import OPS
from rscalc.display import SEGS
from rscalc.machine import build_netlist, FLAGS, WIDTH
import tools.verify_logic as verify_logic
from tools.verify_logic import check_topological, verify


def test_every_operand_pair():
    nl = build_netlist(WIDTH)
    check_topological(nl)
    n = 1 << WIDTH
    t0, total = time.time(), 0
    for op in range(len(OPS)):
        wrong, checked = verify(nl, op, WIDTH, nl.ndigits, range(n))
        assert not wrong, (f"{OPS[op]} is wrong on {len(wrong)} output lines, "
                           f"first {wrong[:3]}")
        total += checked
    lines = total * (nl.ndigits * len(SEGS) + len(FLAGS))
    print(f"  every operand pair: {total:,} pairs over {len(OPS)} operations, "
          f"{lines:,} output lines correct, {time.time()-t0:.0f}s: OK")


def test_the_sweep_can_fail():
    """A sweep that passes on a machine it should reject proves nothing.

    The doc tests learned this the hard way (DESIGN §22), and the lesson is not
    specific to documents: bend one expected answer out of a million and the
    sweep has to notice.
    """
    nl = build_netlist(WIDTH)
    original = verify_logic.expected_lamps

    def bent(op, a, b, width, ndigits):
        segs, flags = original(op, a, b, width, ndigits)
        if (a, b) == (777, 3):
            segs = list(segs)
            segs[0] = "abcdefg" if segs[0] != "abcdefg" else ""
        return segs, flags

    verify_logic.expected_lamps = bent
    try:
        wrong, _ = verify(nl, OPS.index("ADD"), WIDTH, nl.ndigits, [3])
    finally:
        verify_logic.expected_lamps = original
    assert wrong, "one wrong answer in 1,048,576 slipped past the sweep"
    assert all(w[1] == 777 and w[2] == 3 for w in wrong), \
        f"the sweep blamed the wrong vector: {wrong[:3]}"
    print(f"  one bent answer in {1 << WIDTH:,}: caught, and blamed on the "
          f"right operands: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} exhaustive logic tests\n")
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
