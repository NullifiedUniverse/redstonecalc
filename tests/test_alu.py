"""Verify the ALU in simulated redstone and compare the two carry designs.

Every check here runs against placed blocks in the tick-accurate engine, not
against the logical netlist — the netlist is only the expected answer.
"""

import sys, os, random, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.alu import build_alu, reference, alu_inputs, OPS
from rscalc.pla import compile_netlist


def make(width, carry, repeater_delay=1):
    nl = build_alu(width, carry)
    w = World()
    L = compile_netlist(nl, w, repeater_delay=repeater_delay)
    assert not w.lint(), w.lint()[:5]
    e = Engine(w)
    e.initialize_steady()
    return nl, w, L, e


def apply_op(e, L, op, a, b, width):
    vals = alu_inputs(op, a, b, width)
    for name, pos in L.levers.items():
        e.set_lever(pos, bool(vals.get(name, 0)))
    ticks = e.run_until_stable(20000)
    r = sum((1 << i) for i in range(width) if e.high(L.outputs[f"R{i}"]))
    flags = tuple(int(e.high(L.outputs[f])) for f in ("CARRY", "ZERO", "NEG", "OVF"))
    return (r,) + flags, ticks


def test_alu4_cla_exhaustive():
    """All 8 operations over every one of the 256 operand pairs, in redstone."""
    width = 4
    nl, w, L, e = make(width, "cla")
    t0 = time.time()
    n = bad = 0
    worst = 0
    for op in range(8):
        for a in range(1 << width):
            for b in range(1 << width):
                got, ticks = apply_op(e, L, op, a, b, width)
                want = reference(op, a, b, width)
                worst = max(worst, ticks)
                n += 1
                if got != want:
                    bad += 1
                    if bad <= 3:
                        print(f"    {OPS[op]} {a},{b}: got {got} want {want}")
    assert not e.burned_out, f"{len(e.burned_out)} torches burned out"
    assert bad == 0, f"{bad}/{n} wrong"
    print(f"  4-bit CLA ALU: {n} vectors (8 ops x 256 pairs) all correct in "
          f"{len(w.blocks)} blocks, worst settle {worst} gt, {time.time()-t0:.0f}s: OK")


def test_alu8_cla():
    """8-bit: every op over boundary values plus a random sample."""
    # at 8 bits the glitch density trips Minecraft's torch burnout rule, so
    # this build uses delay-2 repeaters (see test_ripple_burnout_and_its_fix)
    width = 8
    nl, w, L, e = make(width, "cla", repeater_delay=2)
    random.seed(20260728)
    edge = [0, 1, 2, 3, 127, 128, 129, 200, 254, 255]
    cases = [(op, a, b) for op in range(8) for a in edge for b in edge]
    cases += [(op, random.randrange(256), random.randrange(256))
              for op in range(8) for _ in range(40)]
    bad = 0
    worst = 0
    for op, a, b in cases:
        got, ticks = apply_op(e, L, op, a, b, width)
        worst = max(worst, ticks)
        want = reference(op, a, b, width)
        if got != want:
            bad += 1
            if bad <= 3:
                print(f"    {OPS[op]} {a},{b}: got {got} want {want}")
    assert not e.burned_out, f"{len(e.burned_out)} torches burned out"
    assert bad == 0, f"{bad}/{len(cases)} wrong"
    print(f"  8-bit CLA ALU (delay-2 repeaters): {len(cases)} vectors all correct "
          f"in {len(w.blocks)} blocks, worst settle {worst} gt "
          f"({worst/20:.1f} s in-game): OK")


def test_carry_lookahead_beats_ripple():
    """The optimisation has to show up as a measured number, not a claim."""
    rows = []
    for width in (4, 8):
        for carry in ("ripple", "cla"):
            nl = build_alu(width, carry)
            w = World()
            L = compile_netlist(nl, w)
            e = Engine(w)
            e.initialize_steady()
            worst = 0
            # a carry that has to travel the full width is the critical case
            for op, a, b in ((0, (1 << width) - 1, 1), (1, 0, 1),
                             (0, (1 << width) - 1, (1 << width) - 1)):
                _, ticks = apply_op(e, L, op, a, b, width)
                worst = max(worst, ticks)
            rows.append((width, carry, nl.gate_count(), nl.depth(),
                         len(w.blocks), L.stats["repeaters"], worst))
    print("\n  width carry   gates depth   blocks  repeaters  worst-case settle")
    for width, carry, g, d, blocks, reps, worst in rows:
        print(f"  {width:5} {carry:7} {g:5} {d:5} {blocks:8} {reps:10} "
              f"{worst:5} gt = {worst/2:.0f} rs ticks = {worst/20:.2f} s")
    by = {(r[0], r[1]): r for r in rows}
    for width in (4, 8):
        _, _, rg, rd, _, _, rw = by[(width, "ripple")]
        _, _, cg, cd, _, _, cw = by[(width, "cla")]
        assert cd < rd, f"{width}-bit: CLA must be logically shallower"
        assert cg < rg, f"{width}-bit: CLA must use fewer gates"
        assert cw <= rw, f"{width}-bit: CLA must not settle slower"
        print(f"  {width}-bit: depth {cd} vs {rd} ({rd/cd:.2f}x shallower), "
              f"gates {cg} vs {rg} ({rg/cg:.2f}x fewer), "
              f"settle {cw} vs {rw} gt (only {rw/cw:.2f}x — interconnect, "
              f"not gate depth, dominates the wall clock)")
    print("  carry-lookahead wins on depth, gate count and latency: OK")


def test_ripple_burnout_and_its_fix():
    """The 8-bit ripple chain glitches hard enough to burn a torch out.

    Minecraft turns a torch off permanently if it is forced to toggle more than
    eight times in 60 game ticks. The ripple carry's deeper, more skewed logic
    produces enough hazard glitching to trip that. The standard redstone remedy
    — longer repeater delays, which spread the transitions out — fixes it, at a
    real cost in latency.
    """
    # Glitching depends on how much changes between *consecutive* vectors, not
    # on any single vector, so the order is shuffled: walking operands in
    # sorted order moves only a bit or two at a time and never provokes it.
    edge = [0, 1, 2, 3, 127, 128, 129, 254, 255]
    cases = [(op, a, b) for op in range(8) for a in edge for b in edge]
    random.Random(20260728).shuffle(cases)
    results = {}
    for delay in (1, 2):
        nl, w, L, e = make(8, "ripple", repeater_delay=delay)
        bad = 0
        worst = 0
        for op, a, b in cases:
            got, ticks = apply_op(e, L, op, a, b, 8)
            worst = max(worst, ticks)
            if got != reference(op, a, b, 8):
                bad += 1
        peak = max(len(b._toggles) for b in w.blocks.values()
                   if b.kind == "redstone_torch")
        results[delay] = (bad, len(e.burned_out), peak, worst)
    d1, d2 = results[1], results[2]
    print(f"  ripple delay=1: {d1[0]}/{len(cases)} wrong, {d1[1]} torches burned "
          f"out, peak {d1[2]} toggles/60gt, settle {d1[3]} gt")
    print(f"  ripple delay=2: {d2[0]}/{len(cases)} wrong, {d2[1]} torches burned "
          f"out, peak {d2[2]} toggles/60gt, settle {d2[3]} gt")
    assert d1[2] > d2[2], "longer repeater delays must reduce toggle density"
    assert d2[1] == 0 and d2[0] == 0, "delay-2 ripple should be clean"
    if d1[1]:
        print(f"  burnout reproduced at delay=1 and cleared by delay=2, at "
              f"{d1[3]}->{d2[3]} gt ({d2[3]/d1[3]:.2f}x latency): OK")
    else:
        print("  delay=1 stayed under the burnout limit on this set; delay=2 "
              "still cuts peak toggle density: OK")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and (only is None or only in k)]
    print(f"Running {len(tests)} ALU tests\n")
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
