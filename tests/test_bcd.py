"""The binary-to-BCD converter, in logic and on placed blocks.

The converter is the piece that lets a binary machine show a decimal answer, so
it gets the strongest test available: every value it can be handed. That is
cheap in logic (2,048 cases for 11 bits) and expensive on blocks, so the placed
build is checked on the corners plus a sample, and the logic exhaustively.
"""

import sys, os, random, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.netlist import Netlist
from rscalc.pla import compile_netlist
from rscalc.bcd import (bin_to_bcd, bcd_reference, digits_needed, add3_cell,
                        add3_reference)

FULL = "--full" in sys.argv


def _converter(nbits):
    nl = Netlist()
    bits = [nl.input(f"V{i}") for i in range(nbits)]
    digits = bin_to_bcd(nl, bits)
    for k, g in enumerate(digits):
        for j, node in enumerate(g):
            nl.output(f"D{k}_{j}", node)
    # The last bit shifted in is never corrected — no digit can reach 5 after
    # it — so bit 0 of the units digit is literally the input wire, with no
    # gate anywhere on the path. Aligning the outputs buffers it up to the
    # others, which is what gives it a rail to be driven from.
    nl.align_outputs()
    return nl, bits, digits


def test_add3_cell():
    """The one piece of arithmetic in the converter, over every BCD digit."""
    nl = Netlist()
    d = [nl.input(f"X{i}") for i in range(4)]
    out = add3_cell(nl, d)
    for v in range(10):
        vals = {f"X{i}": (v >> i) & 1 for i in range(4)}
        e = nl.evaluate(vals)
        got = sum((1 if e[out[j].idx] else 0) << j for j in range(4))
        assert got == add3_reference(v), f"add3({v}) = {got}, want {add3_reference(v)}"
    print(f"  add-3 cell: 0-9 all correct, {nl.gate_count()} gates, "
          f"depth {nl.depth()}: OK")


def test_bin_to_bcd_logic():
    """Every value at 8 and 11 bits — 2,304 cases, exhaustive."""
    for nbits in (8, 11):
        nl, bits, digits = _converter(nbits)
        nd = len(digits)
        assert nd == digits_needed(nbits)
        bad = 0
        for v in range(1 << nbits):
            vals = {f"V{i}": (v >> i) & 1 for i in range(nbits)}
            e = nl.evaluate(vals)
            got = [sum((1 if e[g[j].idx] else 0) << j for j in range(4))
                   for g in digits]
            if got != bcd_reference(v, nd):
                bad += 1
        assert bad == 0, f"{bad}/{1 << nbits} wrong at {nbits} bits"
        print(f"  {nbits} bits -> {nd} digits: all {1 << nbits} values correct, "
              f"{nl.gate_count()} gates, depth {nl.depth()}: OK")


def test_bin_to_bcd_placed():
    """The same converter, built out of blocks and read off the dust."""
    nbits = 10
    nl, bits, digits = _converter(nbits)
    w = World()
    L = compile_netlist(nl, w, repeater_delay=2, drive_inputs=True)
    problems = w.lint()
    assert not problems, problems[:4]
    e = Engine(w)
    e.initialize_steady()

    nd = len(digits)
    cases = [0, 1, 9, 10, 99, 100, 999, 1000, 1023, 512, 511]
    if FULL:
        cases = list(range(1 << nbits))
    else:
        rng = random.Random(11)
        cases += [rng.randrange(1 << nbits) for _ in range(24)]

    t0, worst, bad = time.time(), 0, []
    for v in cases:
        for i in range(nbits):
            e.set_lever(L.levers[f"V{i}"], bool((v >> i) & 1))
        worst = max(worst, e.run_until_stable(40000))
        got = [sum((1 if e.high(L.outputs[f"D{k}_{j}"]) else 0) << j
                   for j in range(4)) for k in range(nd)]
        if got != bcd_reference(v, nd):
            bad.append((v, got, bcd_reference(v, nd)))
    assert not e.burned_out, f"{len(e.burned_out)} torches burned out"
    assert not bad, f"{len(bad)}/{len(cases)} wrong, first {bad[:2]}"
    (x0, y0, z0), (x1, y1, z1) = w.bounds()
    print(f"  placed converter: {len(cases)} values correct on real blocks, "
          f"{len(w.blocks)} blocks, {x1-x0+1}x{y1-y0+1}x{z1-z0+1}, "
          f"worst settle {worst} gt, {time.time()-t0:.0f}s: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} BCD tests\n")
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
