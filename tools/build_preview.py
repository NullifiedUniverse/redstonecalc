"""Build and verify the Mk II preview modules, then export them for the demo."""

import sys, os
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.netlist import Netlist
from rscalc.pla import compile_netlist
from rscalc.export import export_circuit, write_bundle
from rscalc.display import (build_digit, lit_segments, DIGIT_SEGMENTS, SEGS)
from tools.prototype_decimal import seven_seg, build_decimal_adder

LANE_LEN = 6              # how far the feed lanes reach out to -X
DIGIT_PITCH = 14          # X spacing between digits; feed lanes need the room


def build_display(n_digits=3):
    """n digits side by side, most significant on the left.

    Each segment's feed lane ends in a lever, so the digits can be driven by
    hand — the console wires those same lane ends to the decoder instead.
    """
    w = World()
    levers, lamps = {}, {}
    for k in range(n_digits):
        # digit 0 is the least significant, so it sits furthest right
        ox = (n_digits - 1 - k) * DIGIT_PITCH
        _, entries, lp = build_digit(w, origin=(ox, 0, 0), lane_len=LANE_LEN)
        for s in SEGS:
            ex, ey, ez = entries[s]
            w.solid((ex - 1, ey - 1, ez))
            w.lever((ex - 1, ey, ez), attach="down", on=False)
            levers[f"D{k}_{s}"] = (ex - 1, ey, ez)
            lamps[f"D{k}_{s}"] = lp[s]
    return w, levers, lamps


def build_decoder():
    nl = Netlist()
    d = [nl.input(f"D{i}") for i in range(4)]
    segs = seven_seg(nl, d)
    for s in SEGS:
        nl.output(f"SEG_{s}", segs[s])
    w = World()
    L = compile_netlist(nl, w)
    return nl, w, L


def main():
    circuits = []

    # ---------- the 7-segment decoder ----------
    nl, w, L = build_decoder()
    assert not w.lint(), w.lint()[:3]
    e = Engine(w); e.initialize_steady()
    bad = 0
    for k in range(10):
        for i in range(4):
            e.set_lever(L.levers[f"D{i}"], bool((k >> i) & 1))
        e.run_until_stable(4000)
        got = "".join(s for s in SEGS if e.high(L.outputs[f"SEG_{s}"]))
        if got != DIGIT_SEGMENTS[k]:
            bad += 1
            print(f"  decoder digit {k}: {got!r} != {DIGIT_SEGMENTS[k]!r}")
    print(f"decoder: {len(w.blocks)} blocks, depth {nl.depth()}, "
          f"{nl.gate_count()} gates, {'all digits OK' if not bad else f'{bad} WRONG'}")
    circuits.append(export_circuit(
        "seg7", "BCD to seven-segment decoder", w, L,
        {"kind": "decoder", "width": 4, "depth": nl.depth(),
         "gates": nl.gate_count(), "delay": 1}))

    # ---------- the three-digit lamp display ----------
    dw, dlevers, dlamps = build_display(3)
    assert not dw.lint(), dw.lint()[:3]
    de = Engine(dw); de.initialize_steady()
    bad = 0
    for value in (0, 7, 42, 108, 999, 250, 360):
        for k in range(3):
            digit = (value // (10 ** k)) % 10
            want = DIGIT_SEGMENTS[digit]
            for s in SEGS:
                de.set_lever(dlevers[f"D{k}_{s}"], s in want)
        de.run_until_stable(4000)
        for k in range(3):
            digit = (value // (10 ** k)) % 10
            shown = "".join(
                s for s in SEGS
                if all(de.read(p) > 0 for p in dlamps[f"D{k}_{s}"]))
            if shown != DIGIT_SEGMENTS[digit]:
                bad += 1
                print(f"  display {value} digit {k}: {shown!r} != "
                      f"{DIGIT_SEGMENTS[digit]!r}")
    (x0, y0, z0), (x1, y1, z1) = dw.bounds()
    print(f"display: {len(dw.blocks)} blocks, "
          f"{x1-x0+1}x{y1-y0+1}x{z1-z0+1}, "
          f"{'all values OK' if not bad else f'{bad} WRONG'}")

    class _L:                       # the display is hand-placed, not compiled
        levers = dlevers
        outputs = {}
        stats = {"repeaters": 0, "taps": 0,
                 "lamps": sum(len(v) for v in dlamps.values())}
    circuits.append(export_circuit(
        "disp3", "Three-digit seven-segment display", dw, _L,
        {"kind": "display", "width": 3, "depth": 0, "gates": 0, "delay": 1,
         "lamps": {k: [[p[0]-x0, p[1]-y0, p[2]-z0] for p in v]
                   for k, v in dlamps.items()}}))

    print("building the whole console (this takes a moment)...")
    cc = export_console()
    print(f"console: {cc['n']} blocks, dims {cc['dims']}, "
          f"{len(cc['data'])/1024:.0f} KB")
    circuits.append(cc)

    size = write_bundle("out/preview.json", circuits)
    print(f"bundle: {size/1024:.0f} KB -> out/preview.json")




def export_console():
    """Export the whole machine so the browser can drive the real thing."""
    from rscalc.console import build_console
    from rscalc.display import SEGS
    from rscalc.keypad import LATCH_X
    nl, w, L, pads, lamps, stats, ready = build_console(repeater_delay=2)
    assert not w.lint(), w.lint()[:3]
    (x0, y0, z0), (x1, y1, z1) = w.bounds()

    def rel(p):
        return [p[0] - x0, p[1] - y0, p[2] - z0]

    L2 = SimpleNamespace(
        levers={f"{pad}{k}": pads[pad][k]
                for pad in ("A", "B") for k in range(10)},
        outputs={"READY": ready},
        stats=dict(stats, blocks=len(w.blocks)))
    # the one-hot latch of each key, so the page can report the operands the
    # machine is actually holding rather than a JS shadow of what was clicked
    latches = {f"{pad}{k}": rel((LATCH_X,) + tuple(L.levers[f"K{pad}{k}"][1:]))
               for pad in ("A", "B") for k in range(10)}
    meta = {
        "kind": "console", "width": 1, "depth": nl.depth(),
        "gates": nl.gate_count(), "delay": 2,
        "lamps": {f"{d}_{s}": [rel(p) for p in lamps[d][s]]
                  for d in ("0", "1") for s in SEGS},
        "ready": rel(ready),
        "latches": latches,
        "hold": 40,
    }
    return export_circuit("console", "Keypad calculator (whole machine)",
                          w, L2, meta)


if __name__ == "__main__":
    main()
