"""Build and verify the Mk II preview modules, then export them for the demo.

    python3 tools/build_preview.py                 # the Mk III machine
    python3 tools/build_preview.py --delay 3       # a setting §13 says is unsafe
    python3 tools/build_preview.py modules         # the smaller Mk II circuits

This writes `out/preview.json`, which `tools/build_pages.py` embeds in
`docs/preview.html` — so whatever delay it is given is the delay the *published
page* runs at. It used to read `sys.argv` by hand and default that to **3**, at
which §13 measured the machine as 5 vectors in 16 correct with three torches
burned. The README's example passed `4` explicitly, so what shipped was right;
anyone running the tool the obvious way would have replaced it with a machine
that gets the answers wrong.
"""

import argparse
import sys, os
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.netlist import Netlist
from rscalc.pla import compile_netlist
from rscalc.export import export_circuit, write_bundle
from rscalc.display import build_digit, DIGIT_SEGMENTS, SEGS
from tools.prototype_decimal import seven_seg

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
    """Build the bundle the preview page loads.

    The Mk III machine is the page; the smaller circuits are only built when
    asked for, because each one costs a compile and the page never loads them.
    """
    from rscalc.machine import DEFAULT_DELAY
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="?", default="machine",
                    choices=["machine", "modules"],
                    help="the Mk III machine (default) or the Mk II circuits")
    ap.add_argument("--delay", type=int, default=DEFAULT_DELAY,
                    help=f"repeater delay for the machine "
                         f"(default {DEFAULT_DELAY}, the verified setting)")
    args = ap.parse_args()
    only, delay = args.what, args.delay

    if only == "machine":
        if delay != DEFAULT_DELAY:
            # this bundle becomes the published page, so an unverified setting
            # says so on the way past rather than in a bug report
            print(f"WARNING: delay {delay} is not the verified {DEFAULT_DELAY}."
                  f" DESIGN §13 has the table; below 4 the machine burns "
                  f"torches and gets answers wrong.", file=sys.stderr)
        print(f"building the Mk III machine at repeater delay {delay} "
              f"(the first run computes its resting state, which is slow)...")
        cc = export_machine(delay)
        print(f"machine: {cc['n']:,} blocks, dims {cc['dims']}, "
              f"{len(cc['data'])/1024:.0f} KB blocks + "
              f"{len(cc.get('state',''))/1024:.0f} KB state")
        size = write_bundle("out/preview.json", [cc])
        print(f"bundle: {size/1024:.0f} KB -> out/preview.json")
        return
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




def export_machine(delay=None):
    """The whole Mk III machine, with its settled state, for the live page.

    `delay` defaults to what the machine ships at. It used to default to 3,
    which is not a shipping setting — it is the row in §13's table that burns
    torches — and a bundle built at it would have put a machine that computes
    wrong answers on the published page.
    """
    from rscalc.machine import DEFAULT_DELAY
    if delay is None:
        delay = DEFAULT_DELAY
    from rscalc.engine import Engine
    from rscalc.machine import build_machine, FLAGS
    from rscalc.alu import OPS
    from rscalc.steady import settled_engine
    from rscalc.export import encode_world

    m = build_machine(repeater_delay=delay)
    problems = m.world.lint()
    assert not problems, problems[:3]
    e = settled_engine(m.world, Engine, verify=True)
    (x0, y0, z0), _ = m.world.bounds()

    def rel(p):
        return [p[0] - x0, p[1] - y0, p[2] - z0]

    enc = encode_world(m.world, engine=e)
    enc.update({
        "name": "machine",
        "title": f"Mk III — {m.width}-bit calculator",
        "kind": "machine",
        "width": m.width,
        "gates": m.stats["gates"],
        "depth": m.stats["depth"],
        "delay": delay,
        "ops": OPS,
        "flags": FLAGS,
        "digits": [str(k) for k in reversed(range(m.ndigits))],
        "switches": {k: rel(v) for k, v in m.levers.items()},
        "buttons": {str(k): rel(v) for k, v in m.keys.items()},
        # the one-hot rail each key latches into. The page used to show which
        # button was last *clicked*, which is not the same thing as which
        # operation the machine is holding — and when the two disagreed the page
        # confidently displayed the wrong one. Read it off the rails instead.
        "oplatch": {str(k): rel(m.layout.levers[f"K{k}"])
                    for k in range(len(OPS))},
        "lamps": {f"{d}_{s}": [rel(p) for p in m.lamps[d][s]]
                  for d in [str(k) for k in range(m.ndigits)] for s in SEGS},
        "flaglamps": {f: rel(m.lamps["F"][f]) for f in FLAGS},
        "ready": rel(m.ready),
        "hold": 40,
        "stats": dict(m.stats),
        "levers": {}, "outputs": {},
    })
    for f in FLAGS:
        enc["lamps"][f"F_{f}"] = [rel(m.lamps["F"][f])]
    return enc


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
