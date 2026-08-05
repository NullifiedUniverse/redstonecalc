"""The ALU's logic, exhaustively, without placing a block.

`tests/test_alu.py` is the measurement that matters — it drives the ALU through
real levers in the tick-accurate engine — and that is exactly why it is held
back from the fast suite: standing up the blocks and settling them costs
minutes. So the fast suite had no ALU check of its own, and it showed. Two
deliberate breaks in `rscalc/alu.py` — the subtract carry-in, and a left shift
that rotated the top bit round instead of dropping it — were caught by
`test_docs.py`, of all things, which happens to build a machine and quote its
gate count. Incidental coverage is not coverage: it fails for the wrong reason
and it can stop applying the moment a document is edited.

This is the direct check. It evaluates the netlist rather than the redstone,
which is the same split `tests/test_logic.py` and `tools/verify_logic.py` use:
the blocks are verified elsewhere, and what is verified here is what the blocks
were asked to implement. Being logic-only it costs milliseconds, so it can
afford to be exhaustive at four bits and to run both carry designs.
"""

import itertools
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.alu import OPS, alu_inputs, build_alu, reference

CARRIES = ("cla", "ripple")
FLAGS = ("CARRY", "ZERO", "NEG", "OVF")


def read(nl, v, width):
    """The result word and the four flags, off the netlist's own outputs."""
    r = sum(1 << i for i in range(width) if v[nl.outputs[f"R{i}"].idx])
    return r, tuple(int(bool(v[nl.outputs[f].idx])) for f in FLAGS)


def run(nl, op, a, b, width):
    v = nl.evaluate(alu_inputs(op, a, b, width))
    return read(nl, v, width)


def want(op, a, b, width):
    r, carry, zero, neg, ovf = reference(op, a, b, width)
    return r, (carry, zero, neg, ovf)


def test_every_operation_at_four_bits():
    """All eight operations over all 256 operand pairs, both carry designs.

    2,048 vectors per design, and every one of them checks the result *and*
    all four flags — a wrong OVF on one signed edge is exactly the kind of
    fault a sampled test walks past.
    """
    for carry in CARRIES:
        nl = build_alu(4, carry)
        bad = []
        for op in range(len(OPS)):
            for a, b in itertools.product(range(16), repeat=2):
                got, want_ = run(nl, op, a, b, 4), want(op, a, b, 4)
                if got != want_:
                    bad.append(f"{OPS[op]} {a},{b}: {got} want {want_}")
        assert not bad, (f"{carry}: {len(bad)} of 2,048 wrong, e.g. "
                         + "; ".join(bad[:3]))
        print(f"  {carry:6} 4-bit: 8 operations x 256 pairs, result and all "
              f"{len(FLAGS)} flags: OK")


def test_the_two_carry_designs_are_the_same_machine():
    """§4 chose carry-lookahead over ripple for depth, not for behaviour.

    They are two ways to compute the same carries, so any vector on which they
    disagree means one of them is wrong — and this says so without needing to
    know which, which is a check the reference cannot give on its own.
    """
    a_nl, b_nl = (build_alu(4, c) for c in CARRIES)
    for op in range(len(OPS)):
        for a, b in itertools.product(range(16), repeat=2):
            x, y = run(a_nl, op, a, b, 4), run(b_nl, op, a, b, 4)
            assert x == y, f"{OPS[op]} {a},{b}: cla {x} vs ripple {y}"
    print(f"  cla and ripple agree on all {8 * 256:,} vectors: OK")


def test_the_widths_the_machine_is_actually_built_at():
    """Four bits cannot see a width-dependent fault.

    A shift that wraps, a sign bit read at the wrong index, a carry chain whose
    last block is short — those live at the top of the word, and the machine
    ships at ten bits. Every edge of the range, then a seeded sample across it.
    """
    rng = random.Random(20260805)
    for width in (8, 10):
        top = (1 << width) - 1
        sign = 1 << (width - 1)
        edges = [0, 1, top, top - 1, sign, sign - 1, sign + 1]
        pairs = ([(a, b) for a in edges for b in edges]
                 + [(rng.randrange(top + 1), rng.randrange(top + 1))
                    for _ in range(180)])
        for carry in CARRIES:
            nl = build_alu(width, carry)
            for op in range(len(OPS)):
                for a, b in pairs:
                    got, want_ = (run(nl, op, a, b, width),
                                  want(op, a, b, width))
                    assert got == want_, \
                        f"{carry} {width}-bit {OPS[op]} {a},{b}: {got} want {want_}"
        print(f"  {width:2}-bit: {len(pairs)} pairs x {len(OPS)} operations x "
              f"{len(CARRIES)} carry designs, edges included: OK")


def test_the_comparison_can_fail():
    """A test that cannot go red is not a test.

    `reference` is the standard everything above is held to, so a sweep that
    silently compared a thing to itself would pass forever. Break the standard
    by one bit and require the sweep to notice.
    """
    nl = build_alu(4, "cla")
    caught = 0
    for op in range(len(OPS)):
        for a, b in itertools.product(range(16), repeat=2):
            r, flags = want(op, a, b, 4)
            if run(nl, op, a, b, 4) != (r ^ 1, flags):
                caught += 1
    assert caught == len(OPS) * 256, \
        (f"flipping the reference's low bit went unnoticed on "
         f"{len(OPS) * 256 - caught} vectors")
    print(f"  a one-bit lie in the reference is caught on every one of "
          f"{caught:,} vectors: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} ALU logic tests\n")
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
