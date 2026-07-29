"""The whole calculator in one world: keypads, adder, loom, display.

    two 10-key pads  ->  one-hot latches  ->  BCD encode
                      ->  one-digit decimal adder
                      ->  two seven-segment decoders
                      ->  wiring loom
                      ->  two lamp digits

Every part is redstone. The keypads latch with locked repeaters, the arithmetic
is a compiled gate array, and the loom carries the fourteen segment lines to the
two digits without a single crossing. A player presses A, presses B, and reads
the sum off the lamps.

The two digits sit on their own feed levels — tens above units — so the units
digit's lanes can pass beneath the tens digit without meeting it, while both
sets of lamps still end up on one plane and read as a single number.
"""

from __future__ import annotations

from .engine import World
from .netlist import Netlist
from .pla import compile_netlist
from .display import build_digit, lit_segments, read_digit, SEGS
from .harness import route
from .keypad import build_keypad, press


def _seven_seg(nl, d):
    """4-bit BCD digit -> seven segment lines, minterms shared. Two stages."""
    from .display import DIGIT_SEGMENTS
    nmin = {k: nl.gate([(d[i], bool((k >> i) & 1)) for i in range(4)])
            for k in range(10)}
    return {s: nl.gate([(nmin[k], True) for k in range(10)
                        if s in DIGIT_SEGMENTS[k]]) for s in SEGS}


def _add4(nl, a, b, cin):
    """4-bit adder, flat carry-lookahead: every carry is two stages."""
    P = [nl.or_(a[i], b[i]) for i in range(4)]
    C = [cin]
    for k in range(4):
        terms = []
        for j in range(k, -1, -1):
            lits = [(P[m], True) for m in range(j + 1, k + 1)]
            lits += [(a[j], True), (b[j], True)]
            terms.append(nl.gate(lits))
        terms.append(nl.gate([(P[m], True) for m in range(0, k + 1)]
                             + [(cin, True)]))
        C.append(nl.gate([(t, True) for t in terms]))
    return [nl.xor(nl.xor(a[i], b[i]), C[i]) for i in range(4)], C[4]


def build_netlist():
    """One-hot keys in, fourteen segment lines out."""
    nl = Netlist()
    KA = [nl.input(f"KA{k}") for k in range(10)]
    # Each keypad has its own "any key is down" bus running the length of its
    # rails. Declared back to back the two bands would touch, the buses would
    # merge, and pressing a B key would wipe the A latches. Three spacer rails
    # keep the bands apart; they are never driven.
    GAP = [nl.input(f"GAP{i}") for i in range(3)]
    KB = [nl.input(f"KB{k}") for k in range(10)]
    z = nl.zero()

    # one-hot -> BCD is just an OR per bit over the keys that carry it
    A = [nl.or_(*[KA[k] for k in range(10) if (k >> j) & 1]) for j in range(4)]
    B = [nl.or_(*[KB[k] for k in range(10) if (k >> j) & 1]) for j in range(4)]

    # one decimal digit: binary add, then +6 whenever the result passes 9
    S, c4 = _add4(nl, A, B, z)
    carry = nl.or_(c4, nl.and_(S[3], nl.or_(S[2], S[1])))
    units, _ = _add4(nl, S, [z, carry, carry, z], z)

    for s, node in _seven_seg(nl, units).items():
        nl.output(f"SEG0_{s}", node)
    # The tens digit is only ever blank or 1, so it needs no decoder at all —
    # just the two segments that draw a 1, straight off the carry. Leaving it
    # blank instead of showing a leading zero is what a calculator does.
    for s in ("b", "c"):
        nl.output(f"SEG1_{s}", nl.buf(carry))

    # Key 0 encodes to all-bits-low, so on its own it drives nothing and would
    # be optimised away — yet its latch still has to exist, because pressing it
    # is what clears the other nine. A "both operands entered" lamp keeps every
    # key in the netlist, and is worth having anyway.
    nl.output("READY", nl.and_(nl.or_(*KA, *GAP), nl.or_(*KB)))
    return nl


def build_console(repeater_delay=1):
    """Place the whole machine. Returns everything a test needs to drive it."""
    nl = build_netlist()
    w = World()
    L = compile_netlist(nl, w, repeater_delay=repeater_delay,
                        drive_inputs=False, fixed_input_order=True)
    (x0, y0, z0), (x1, y1, z1) = w.bounds()

    # keypads drive the primary input rails directly
    pads = {}
    for pad in ("A", "B"):
        targets = [L.levers[f"K{pad}{k}"] for k in range(10)]
        pads[pad] = build_keypad(w, targets)

    outs = {name: pos for name, pos in L.outputs.items()}
    ready = outs.pop("READY")
    y_out = max(p[1] for p in list(outs.values()) + [ready])
    ya, yb = y_out + 4, y_out + 8
    # tens sits one feed level above units, so the units lanes pass beneath it;
    # the differing feed drops put both sets of lamps on the same plane
    y_disp = {"0": yb + 4, "1": yb + 8}
    lamp_y = yb + 16

    digits, lamps = {}, {}
    span = 40
    for k in ("1", "0"):                     # tens on the left, units right
        ox = x1 + 70 + (0 if k == "1" else span)
        _, entries, lp = build_digit(
            w, origin=(ox, lamp_y, 0),
            feed_drop=lamp_y - y_disp[k], lane_len=1)
        digits[k] = entries
        lamps[k] = lp

    nets = []
    for k in ("0", "1"):
        for s in SEGS:
            if f"SEG{k}_{s}" in outs:      # the tens digit only drives b and c
                nets.append((outs[f"SEG{k}_{s}"], digits[k][s], y_disp[k]))
    nets.sort(key=lambda n: n[0][2])

    # a lamp beside the READY rail, lit once both operands are in
    rx, ry, rz = ready
    for dx in (1, 2):
        w.solid((rx + dx, ry - 1, rz)); w.wire((rx + dx, ry, rz))
    w.lamp((rx + 3, ry, rz))
    ready_lamp = (rx + 3, ry, rz)
    stats = {"nets": 0, "towers": 0}
    for i, (src, dst, yd) in enumerate(nets):
        st = route(w, [(src, dst)], ya, yb, yd, turn_x0=x1 + 6 + 4 * i)
        stats["nets"] += st["nets"]; stats["towers"] += st["towers"]
    return nl, w, L, pads, lamps, stats, ready_lamp


def read_display(engine, lamps):
    """The number on the lamps, or None if either digit is not a numeral."""
    shown = lit_segments(engine, lamps["1"])
    tens = 0 if shown == "" else read_digit(engine, lamps["1"])
    units = read_digit(engine, lamps["0"])
    if tens is None or units is None:
        return None
    return tens * 10 + units
