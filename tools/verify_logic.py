"""Every operand pair the machine can be given, through the whole logic chain.

The placed machine is verified on the blocks, driven through the wall levers —
`verify_full.py` does that, and it is the measurement that matters, because it
is the only one that involves redstone. But it runs 300 vectors, because
settling half a million blocks takes about a second each and 8 × 1,048,576 of
those is three months.

This verifies the other half of the claim: not that the blocks carry the logic,
but that the *logic is right*, over every input it can ever be given. All eight
operations, all 1,048,576 operand pairs each, checked at the far end of the
chain — the seven-segment lines that drive the lamps and the four flag outputs,
after the ALU, the binary-to-BCD conversion, the decoders and leading-zero
blanking. 8,388,608 lamp readings and 33,554,432 segment lines, in about a
minute.

What makes that affordable is bit slicing. A gate here is an OR of literals, so
evaluating it on one vector or on a thousand costs the same instruction if the
thousand are packed into the bits of one integer. Python's integers are already
arbitrary-width, so a whole 1,024-vector sweep of A is one pass over the netlist
with 1,024-bit words, and the outer loop is over B alone.

    python3 tools/verify_logic.py            # all eight, exhaustive
    python3 tools/verify_logic.py --op ADD
    python3 tools/verify_logic.py --sample 64 # a quick slice of each

It does *not* verify the redstone: no block is placed and no tick is simulated.
It verifies what the redstone was asked to implement. The two halves are
deliberately separate — `verify_full.py` and `tests/test_machine.py` are where
placement and timing get their evidence.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.alu import OPS, reference
from rscalc.display import DIGIT_SEGMENTS, SEGS
from rscalc.machine import build_netlist, FLAGS, WIDTH


def check_topological(nl):
    """Bit slicing evaluates in list order, so that order has to be a valid one.

    `Netlist` appends nodes as they are built, which makes this true by
    construction — but it is true by construction of something else, and the
    whole file is wrong in a way that produces plausible answers if it ever
    stops being. One pass, and then it can be relied on.
    """
    for n in nl.nodes:
        for src, _ in n.taps:
            assert src < n.idx, (f"node {n.idx} reads {src}, which is built "
                                 f"later — list order is not topological")


def expected_lamps(op, a, b, width, ndigits):
    """What the lamps should read: segment sets per digit, and the flags.

    Leading zeros are blanked, so a digit above the value's own is dark rather
    than showing a 0 — `machine.build_netlist` folds that into the decoder, and
    it has to be modelled here or every result under 1000 looks wrong.
    """
    r, carry, zero, neg, ovf = reference(op, a, b, width)
    segs = []
    for k in range(ndigits):
        digit = (r // (10 ** k)) % 10
        blank = k > 0 and r < 10 ** k
        segs.append("" if blank else DIGIT_SEGMENTS[digit])
    return segs, {"CARRY": carry, "ZERO": zero, "NEG": neg, "OVF": ovf}


def verify(nl, op, width, ndigits, bs, report=None):
    """One operation, every pair of operands, in `len(bs)` passes.

    Each pass fixes B and sweeps A across the bits of one integer, so the netlist
    is walked once per B rather than once per pair. The comparison is between
    integers too: the expected lane for an output is assembled from `reference`
    over the same 1,024 values of A, and one `!=` covers the lot.
    """
    n = 1 << width
    mask = (1 << n) - 1
    nodes = nl.nodes
    a_lane = {f"A{i}": sum(1 << j for j in range(n) if (j >> i) & 1)
              for i in range(width)}
    outs = ([(f"SEG{k}_{s}", k, s) for k in range(ndigits) for s in SEGS]
            + [(f, None, f) for f in FLAGS])
    out_idx = {name: nl.outputs[name].idx for name, _, _ in outs}

    wrong, checked = [], 0
    for b in bs:
        values = dict(a_lane)
        for i in range(width):
            values[f"B{i}"] = mask if (b >> i) & 1 else 0
        for k in range(len(OPS)):
            values[f"K{k}"] = mask if k == op else 0

        v = [0] * len(nodes)
        for node in nodes:
            if node.kind == "input":
                v[node.idx] = values.get(node.name, 0)
            else:
                acc = 0
                for src, inv in node.taps:
                    acc |= (~v[src] & mask) if inv else v[src]
                v[node.idx] = acc

        # the same 1,024 answers, assembled into lanes the same way
        want = {name: 0 for name, _, _ in outs}
        for a in range(n):
            segs, flags = expected_lamps(op, a, b, width, ndigits)
            for name, k, s in outs:
                on = flags[s] if k is None else (s in segs[k])
                if on:
                    want[name] |= 1 << a
        checked += n

        for name, k, s in outs:
            got = v[out_idx[name]] & mask
            if got == want[name]:
                continue
            differ = got ^ want[name]
            a = (differ & -differ).bit_length() - 1      # lowest failing vector
            wrong.append((op, a, b, name, bool(got >> a & 1)))
            if len(wrong) > 20:
                return wrong, checked
        if report:
            report(b)
    return wrong, checked


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--op", action="append", choices=OPS,
                    help="only this operation (repeatable)")
    ap.add_argument("--sample", type=int, default=0,
                    help="check only this many values of B, evenly spread")
    ap.add_argument("--width", type=int, default=WIDTH)
    args = ap.parse_args()

    t0 = time.time()
    nl = build_netlist(args.width)
    check_topological(nl)
    ndigits = nl.ndigits
    n = 1 << args.width
    ops = [OPS.index(o) for o in (args.op or OPS)]
    bs = (list(range(0, n, max(1, n // args.sample)))[:args.sample]
          if args.sample else list(range(n)))

    print(f"{nl.gate_count()} gates, depth {nl.depth()}, {args.width} bits: "
          f"{len(ops)} operations x {len(bs)} x {n} operand pairs")
    total, failures = 0, []
    for op in ops:
        t = time.time()
        wrong, checked = verify(nl, op, args.width, ndigits, bs)
        total += checked
        failures += wrong
        mark = "OK " if not wrong else f"{len(wrong)} WRONG"
        print(f"  {OPS[op]:4} {checked:>9,} pairs  {time.time()-t:5.1f}s  {mark}")
        for op_, a, b, name, got in wrong[:5]:
            print(f"       {OPS[op_]} {a} {b}: {name} reads {int(got)}")

    print(f"\n{total:,} operand pairs, "
          f"{total * (ndigits * len(SEGS) + len(FLAGS)):,} output lines, "
          f"{time.time()-t0:.0f}s")
    if failures:
        print(f"FAIL — {len(failures)} wrong output lines")
        return 1
    print("all correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
