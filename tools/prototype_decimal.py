"""Measure the decimal (BCD) proposal instead of guessing at it.

The current calculator is binary, so showing a result needs a binary->decimal
conversion, which is expensive in redstone. If the machine works in BCD from
the start, the display is a flat 4->7 lookup with no conversion at all.

This builds both halves for real, compiles them to blocks and simulates them,
so the plan can quote measured numbers.

    python3 tools/prototype_decimal.py            # the decoder and the adder
    python3 tools/prototype_decimal.py --carry    # ripple vs digit lookahead

The second is where PLAN.md's cross-digit lookahead table comes from. It used to
sit below the `__main__` guard, which meant the code behind a documented
measurement could not be run at all without editing the file.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.netlist import Netlist
from rscalc.pla import compile_netlist

# segment patterns a,b,c,d,e,f,g for digits 0-9
SEVEN_SEG = {
    0: "abcdef", 1: "bc",   2: "abdeg",  3: "abcdg", 4: "bcfg",
    5: "acdfg",  6: "acdefg", 7: "abc",  8: "abcdefg", 9: "abcdfg",
}
SEGS = "abcdefg"


def add4(nl, a, b, cin):
    """4-bit adder with a flat carry-lookahead: every carry is two stages."""
    P = [nl.or_(a[i], b[i]) for i in range(4)]
    C = [cin]
    for k in range(4):
        terms = []
        for j in range(k, -1, -1):
            lits = [(P[m], True) for m in range(j + 1, k + 1)]
            lits += [(a[j], True), (b[j], True)]
            terms.append(nl.gate(lits))
        lits = [(P[m], True) for m in range(0, k + 1)] + [(cin, True)]
        terms.append(nl.gate(lits))
        C.append(nl.gate([(t, True) for t in terms]))
    S = [nl.xor(nl.xor(a[i], b[i]), C[i]) for i in range(4)]
    return S, C[4]


def bcd_digit_add(nl, a, b, cin):
    """One decimal digit: binary add, then +6 whenever the result exceeds 9."""
    S, c4 = add4(nl, a, b, cin)
    gt9 = nl.and_(S[3], nl.or_(S[2], S[1]))       # 1010..1111
    need = nl.or_(c4, gt9)
    z = nl.zero()
    R, _ = add4(nl, S, [z, need, need, z], z)     # +6 == +0110
    return R, need


def seven_seg(nl, d):
    """4-bit BCD digit -> 7 segment lines. Two stages, minterms shared."""
    nmin = {}
    for k in range(10):
        lits = [(d[i], bool((k >> i) & 1)) for i in range(4)]
        nmin[k] = nl.gate(lits)                   # NOT(digit == k)
    out = {}
    for s in SEGS:
        on = [k for k in range(10) if s in SEVEN_SEG[k]]
        out[s] = nl.gate([(nmin[k], True) for k in on])
    return out


def build_display_only():
    nl = Netlist()
    d = [nl.input(f"D{i}") for i in range(4)]
    segs = seven_seg(nl, d)
    for s in SEGS:
        nl.output(f"SEG_{s}", segs[s])
    return nl


def build_decimal_adder(digits=3, with_display=True):
    nl = Netlist()
    A = [[nl.input(f"A{k}_{i}") for i in range(4)] for k in range(digits)]
    B = [[nl.input(f"B{k}_{i}") for i in range(4)] for k in range(digits)]
    carry = nl.zero()
    for k in range(digits):
        R, carry = bcd_digit_add(nl, A[k], B[k], carry)
        for i in range(4):
            nl.output(f"R{k}_{i}", R[i])
        if with_display:
            segs = seven_seg(nl, R)
            for s in SEGS:
                nl.output(f"SEG{k}_{s}", segs[s])
    nl.output("COUT", nl.buf(carry))
    return nl


def measure(nl, label, repeater_delay=2):
    """Compile, lint, settle. Delay 2 because that is what these builds need.

    It defaulted to 1, which is the setting PLAN §5 measures as burning torches
    out and getting the wrong answer on exactly these circuits — so the numbers
    in PLAN's tables, which say "all at delay-2 repeaters", could not have come
    from running this file as it stood.
    """
    w = World()
    L = compile_netlist(nl, w, repeater_delay=repeater_delay)
    problems = w.lint()
    assert not problems, problems[:3]
    (x0, y0, z0), (x1, y1, z1) = w.bounds()
    e = Engine(w)
    e.initialize_steady()
    return nl, w, L, e, {
        "label": label, "gates": nl.gate_count(), "depth": nl.depth(),
        "blocks": len(w.blocks), "reps": L.stats["repeaters"],
        "dims": (x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1),
    }


def set_digits(e, L, prefix, digits, value):
    for k in range(digits):
        d = (value // (10 ** k)) % 10
        for i in range(4):
            e.set_lever(L.levers[f"{prefix}{k}_{i}"], bool((d >> i) & 1))


def main():
    rows = []

    # --- the display decoder on its own ---
    nl, w, L, e, m = measure(build_display_only(), "7-segment decoder (1 digit)")
    bad = 0
    worst = 0
    for k in range(16):
        for i in range(4):
            e.set_lever(L.levers[f"D{i}"], bool((k >> i) & 1))
        worst = max(worst, e.run_until_stable(4000))
        got = "".join(s for s in SEGS if e.high(L.outputs[f"SEG_{s}"]))
        want = SEVEN_SEG.get(k, "")
        if k < 10 and got != want:
            bad += 1
            print(f"    digit {k}: got {got!r} want {want!r}")
    m["settle"] = worst
    m["check"] = f"digits 0-9 correct" if bad == 0 else f"{bad} WRONG"
    rows.append(m)

    # --- a 3-digit decimal adder, display included ---
    digits = 3
    nl, w, L, e, m = measure(build_decimal_adder(digits, True),
                             f"{digits}-digit BCD adder + display")
    bad = 0
    worst = 0
    cases = [(0, 0), (1, 1), (9, 1), (99, 1), (999, 1), (123, 456),
             (555, 445), (500, 500), (250, 250), (99, 99), (909, 91)]
    for a, b in cases:
        set_digits(e, L, "A", digits, a)
        set_digits(e, L, "B", digits, b)
        worst = max(worst, e.run_until_stable(20000))
        got = 0
        for k in range(digits):
            d = sum((1 << i) for i in range(4)
                    if e.high(L.outputs[f"R{k}_{i}"]))
            got += d * (10 ** k)
        if e.high(L.outputs["COUT"]):
            got += 10 ** digits
        if got != a + b:
            bad += 1
            print(f"    {a} + {b} = {got}, expected {a+b}")
        # the display must agree with the digits it is showing
        for k in range(digits):
            d = (got // (10 ** k)) % 10
            shown = "".join(s for s in SEGS if e.high(L.outputs[f"SEG{k}_{s}"]))
            if shown != SEVEN_SEG[d]:
                bad += 1
                print(f"    digit {k} shows {shown!r}, expected {SEVEN_SEG[d]!r}")
    m["settle"] = worst
    m["check"] = f"{len(cases)} sums + display correct" if bad == 0 else f"{bad} WRONG"
    rows.append(m)

    print("\nMeasured, on placed blocks\n")
    print(f"{'build':34} {'gates':>6} {'depth':>6} {'blocks':>8} {'reps':>6} "
          f"{'settle':>8}  {'dims':>14}  check")
    for r in rows:
        d = "x".join(str(v) for v in r["dims"])
        print(f"{r['label']:34} {r['gates']:>6} {r['depth']:>6} {r['blocks']:>8} "
              f"{r['reps']:>6} {r['settle']:>5} gt  {d:>14}  {r['check']}")


# --- cross-digit carry lookahead -------------------------------------------

def bcd_gp(nl, a, b):
    """Per-digit generate/propagate, from the operands alone.

    A decimal digit carries when a+b+cin exceeds 9, so it *generates* when
    a+b >= 10 and *propagates* when a+b == 9. Both depend only on that digit's
    own operands, which is exactly what lets the carry be looked ahead across
    digits instead of rippling through them.
    """
    S, c4 = add4(nl, a, b, nl.zero())          # a + b, five bits
    g = nl.or_(c4, nl.and_(S[3], nl.or_(S[2], S[1])))          # >= 10
    np_ = nl.gate([(c4, False), (S[3], True), (S[2], False),
                   (S[1], False), (S[0], True)])               # NOT(== 9)
    return S, c4, g, nl.not_(np_)


def build_decimal_adder_cla(digits=3, with_display=True):
    """BCD adder whose digit carries are looked ahead, not rippled."""
    nl = Netlist()
    A = [[nl.input(f"A{k}_{i}") for i in range(4)] for k in range(digits)]
    B = [[nl.input(f"B{k}_{i}") for i in range(4)] for k in range(digits)]
    z = nl.zero()

    sums, G, P = [], [], []
    for k in range(digits):
        S, c4, g, p = bcd_gp(nl, A[k], B[k])
        sums.append(S); G.append(g); P.append(p)

    # flat sum-of-products per carry: two stages regardless of digit count
    C = [z]
    for k in range(digits):
        terms = []
        for j in range(k, -1, -1):
            lits = [(P[m], True) for m in range(j + 1, k + 1)] + [(G[j], True)]
            terms.append(nl.gate(lits))
        lits = [(P[m], True) for m in range(0, k + 1)] + [(C[0], True)]
        terms.append(nl.gate(lits))
        C.append(nl.gate([(t, True) for t in terms], name=f"C{k+1}"))

    # result = (a+b) + cin + 6*cout, all mod 16 — one add, no correction pass
    for k in range(digits):
        addend = [C[k], C[k + 1], C[k + 1], z]        # 0, 1, 6 or 7
        R, _ = add4(nl, sums[k], addend, z)
        for i in range(4):
            nl.output(f"R{k}_{i}", R[i])
        if with_display:
            segs = seven_seg(nl, R)
            for s in SEGS:
                nl.output(f"SEG{k}_{s}", segs[s])
    nl.output("COUT", nl.buf(C[digits]))
    return nl


def compare_bcd():
    rows = []
    for label, builder in (("ripple digit carry", build_decimal_adder),
                           ("digit carry lookahead", build_decimal_adder_cla)):
        nl = builder(3, True)
        _, w, L, e, m = measure(nl, label)
        bad = 0
        worst = 0
        cases = [(0, 0), (1, 1), (9, 1), (99, 1), (999, 1), (123, 456),
                 (555, 445), (500, 500), (250, 250), (99, 99), (909, 91),
                 (999, 999), (450, 550), (1, 999)]
        for a, b in cases:
            set_digits(e, L, "A", 3, a)
            set_digits(e, L, "B", 3, b)
            worst = max(worst, e.run_until_stable(30000))
            got = 0
            for k in range(3):
                d = sum((1 << i) for i in range(4)
                        if e.high(L.outputs[f"R{k}_{i}"]))
                got += d * (10 ** k)
            if e.high(L.outputs["COUT"]):
                got += 1000
            if got != a + b:
                bad += 1
                if bad <= 2:
                    print(f"    {label}: {a}+{b} = {got}, expected {a+b}")
            for k in range(3):
                d = (got // (10 ** k)) % 10
                shown = "".join(s for s in SEGS
                                if e.high(L.outputs[f"SEG{k}_{s}"]))
                if shown != SEVEN_SEG[d]:
                    bad += 1
        m["settle"] = worst
        m["check"] = f"{len(cases)} sums + display OK" if not bad else f"{bad} WRONG"
        rows.append(m)
    print(f"\n{'BCD adder':24} {'gates':>6} {'depth':>6} {'blocks':>8} "
          f"{'reps':>6} {'settle':>8}  check")
    for r in rows:
        print(f"{r['label']:24} {r['gates']:>6} {r['depth']:>6} {r['blocks']:>8} "
              f"{r['reps']:>6} {r['settle']:>5} gt  {r['check']}")


if __name__ == "__main__":
    compare_bcd() if "--carry" in sys.argv[1:] else main()
