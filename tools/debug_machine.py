"""Bisect the machine: is the logic wrong, the placement wrong, or the loom?

Three layers are compared for the same inputs:

  1. the netlist, evaluated as pure logic
  2. the compiled outputs, read off the dust where the PLA leaves them
  3. the lamps, at the far end of the wiring loom

Whichever comparison first disagrees is where the fault is.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import Engine
from rscalc.machine import build_machine, FLAGS
from rscalc.alu import OPS
from rscalc.display import SEGS, lit_segments
from rscalc.steady import settled_engine

DELAY = int(sys.argv[1]) if len(sys.argv) > 1 else 2


def main():
    m = build_machine(repeater_delay=DELAY)
    e = settled_engine(m.world, Engine)
    L = m.layout

    for op, a, b in [(0, 0, 0), (0, 7, 5), (0, 1023, 1023), (1, 1000, 1)]:
        m.set_operands(e, a, b)
        m.press_op(e, op)
        e.run_until_stable(400000)

        # what the input rails actually hold, read off the blocks
        vals = {}
        for name, pos in L.levers.items():
            vals[name] = 1 if e.read(pos) > 0 else 0
        got_op = sum(vals.get(f"K{k}", 0) << 0 for k in range(8))
        onehot = [k for k in range(8) if vals.get(f"K{k}")]
        ra = sum(vals.get(f"A{i}", 0) << i for i in range(m.width))
        rb = sum(vals.get(f"B{i}", 0) << i for i in range(m.width))

        ref = m.nl.evaluate(vals)
        want, wflags = m.expect(op, a, b)

        # layer 2: compiled outputs vs the netlist's own answer
        bad_pla = []
        for name, node in m.nl.outputs.items():
            if name not in L.outputs:
                continue
            logic = bool(ref[node.idx])
            placed = e.high(L.outputs[name])
            if logic != placed:
                bad_pla.append((name, logic, placed))

        # layer 3: lamps vs the compiled outputs they are fed from
        bad_loom = []
        for k in range(m.ndigits):
            for s in SEGS:
                src = L.outputs.get(f"SEG{k}_{s}")
                if src is None:
                    continue
                at_src = e.high(src)
                at_lamp = all(e.read(p) > 0 for p in m.lamps[str(k)][s])
                if at_src != at_lamp:
                    bad_loom.append((f"SEG{k}_{s}", at_src, at_lamp))
        for f in FLAGS:
            src = L.outputs.get(f)
            at_lamp = e.read(m.lamps["F"][f]) > 0
            if e.high(src) != at_lamp:
                bad_loom.append((f, e.high(src), at_lamp))

        shown = "|".join(lit_segments(e, m.lamps[str(k)])
                         for k in reversed(range(m.ndigits)))
        print(f"{OPS[op]} {a},{b}: rails A={ra} B={rb} keys={onehot}")
        print(f"   want {want} {wflags}")
        print(f"   lamps show [{shown}] -> {m.read_value(e)} "
              f"flags {m.read_flags(e)}")
        print(f"   PLA disagrees with logic on {len(bad_pla)} outputs "
              f"{bad_pla[:4]}")
        print(f"   loom disagrees with PLA on {len(bad_loom)} lines "
              f"{bad_loom[:4]}")
        # and what the logic itself says the digits should be
        want_segs = []
        for k in reversed(range(m.ndigits)):
            on = "".join(s for s in SEGS
                         if f"SEG{k}_{s}" in m.nl.outputs
                         and ref[m.nl.outputs[f"SEG{k}_{s}"].idx])
            want_segs.append(on)
        print(f"   logic says digits should be [{'|'.join(want_segs)}]")
        print(f"   burned {len(e.burned_out)}")


if __name__ == "__main__":
    main()
