"""The netlist-level helpers, against truth tables.

`logic.py` is the layer between "what the machine should compute" and the gates
that compute it: sums of products, one-hot encode and decode, and the folding
that makes a constant input disappear instead of costing a gate. Everything in
it was reachable only through the ALU and the machine, which meant a helper
nothing happened to call was a helper nothing checked. These are cheap — they
evaluate the netlist rather than placing blocks — so they check every input.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.netlist import Netlist
from rscalc.logic import sop, all_of, any_of, decode_onehot, encode_onehot


def truth(nl, inputs, out, fn, label):
    """Evaluate the netlist over every assignment and compare with `fn`."""
    bad = 0
    for bits in itertools.product([0, 1], repeat=len(inputs)):
        vals = dict(zip(inputs, bits))
        got = bool(nl.evaluate(vals)[out.idx])
        want = bool(fn(bits))
        if got != want:
            bad += 1
            if bad <= 3:
                print(f"    {label} {bits}: got {got} want {want}")
    assert bad == 0, f"{label}: {bad} mismatches"


def test_sop_matches_every_two_level_expression():
    """Random sums of products of three variables, all eight assignments."""
    import random
    rng = random.Random(3)
    for trial in range(24):
        nl = Netlist()
        names = ["A", "B", "C"]
        ins = [nl.input(n) for n in names]
        terms = []
        for _ in range(rng.randint(1, 3)):
            lits = [(i, rng.choice([True, False]))
                    for i in rng.sample(range(3), rng.randint(1, 3))]
            terms.append([(ins[i], pol) for i, pol in lits])
        out = sop(nl, terms)

        def want(bits, terms=terms, ins=ins):
            for term in terms:
                if all(bool(bits[ins.index(n)]) == pol for n, pol in term):
                    return 1
            return 0

        truth(nl, names, out, want, f"sop#{trial}")
    print("  sop: 24 random two-level expressions, all 8 assignments: OK")


def test_all_of_and_any_of():
    for n in (1, 2, 3, 4):
        names = [f"X{i}" for i in range(n)]

        nl = Netlist()
        ins = [nl.input(x) for x in names]
        truth(nl, names, all_of(nl, ins), lambda b: int(all(b)), f"all_of{n}")

        nl = Netlist()
        ins = [nl.input(x) for x in names]
        truth(nl, names, any_of(nl, ins), lambda b: int(any(b)), f"any_of{n}")
    print("  all_of / any_of: widths 1-4, every assignment: OK")


def test_any_of_folds_constants_away():
    """A constant input should disappear, not cost a gate."""
    nl = Netlist()
    a = nl.input("A")
    one = nl.one()          # materialise it first: `one` is itself one gate,
    before = nl.gate_count()  # so the baseline has to be taken after it exists
    assert nl.is_const(any_of(nl, [a, one])) == 1, "OR with 1 is 1"
    assert nl.gate_count() == before, "folding to a constant costs no gate"
    assert nl.is_const(any_of(nl, [nl.zero(), nl.zero()])) == 0, "OR of 0s is 0"
    # a live input beside a dead one keeps just the live one
    nl2 = Netlist()
    b = nl2.input("B")
    truth(nl2, ["B"], any_of(nl2, [b, nl2.zero()]), lambda x: x[0], "any_of+0")
    print("  any_of: constants fold, no gate spent: OK")


def test_decode_onehot_is_the_complement_of_equality():
    """`decode_onehot(bits, k)` is NOT(bits == k) — the ALU's opcode decode."""
    for nbits in (2, 3):
        names = [f"OP{i}" for i in range(nbits)]
        for k in range(1 << nbits):
            nl = Netlist()
            ins = [nl.input(x) for x in names]
            out = decode_onehot(nl, ins, k)
            val = lambda b: sum(v << i for i, v in enumerate(b))
            truth(nl, names, out, lambda b, k=k: int(val(b) != k),
                  f"decode{nbits}/{k}")
            assert nl.depth() == 1, "an OR of literals is one stage"
    print("  decode_onehot: 2 and 3 bits, every k, one stage each: OK")


def test_encode_onehot_round_trips_with_decode():
    """One-hot in, binary out — and the ALU's decode inverts it."""
    for nbits in (2, 3):
        n = 1 << nbits
        names = [f"K{k}" for k in range(n)]
        nl = Netlist()
        lines = [nl.input(x) for x in names]
        word = encode_onehot(nl, lines, nbits)
        for hot in range(n):
            vals = {names[k]: int(k == hot) for k in range(n)}
            ev = nl.evaluate(vals)
            got = sum((1 if ev[word[j].idx] else 0) << j for j in range(nbits))
            assert got == hot, f"one-hot {hot} encoded as {got}"
        assert nl.depth() <= 1, "one OR per output bit is one stage"
    print("  encode_onehot: 2 and 3 bits, every one-hot line: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} logic tests\n")
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
