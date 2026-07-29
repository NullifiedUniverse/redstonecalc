"""Mk III — a 10-bit calculator, complete, in one world.

    20 operand levers  ->  A, B
    8-button keypad    ->  one-hot latches -> 3-bit opcode
                       ->  10-bit ALU (8 operations, 4 flags)
                       ->  binary to BCD (double dabble)
                       ->  four seven-segment decoders
                       ->  wiring loom
                       ->  four lamp digits + a flag row

Every stage is redstone. The pieces are separate modules with their own tests —
`alu`, `bcd`, `display`, `keypad`, `harness`, `logic` — and this file is the
only place that knows how they fit together.

Why one netlist rather than several placed side by side: the compiler stacks
each logic stage on its own Y plane, and a stage's rail length is set by how
many gates sit in *that* stage. Pipelining the ALU into the converter into the
decoders uses disjoint stages, so merging them costs no extra rail — while
splitting them into separately placed modules would add an inter-module loom
for every one of the 14 signals crossing each boundary. The modules are in the
source; the silicon is one block.
"""

from __future__ import annotations

from .engine import World
from .netlist import Netlist
from .pla import compile_netlist
from .alu import alu_core, OPS, reference
from .bcd import bin_to_bcd, digits_needed
from .display import (build_digit, build_lamp_row, seven_seg, lit_segments,
                      read_digit, SEGS)
from .harness import route, TURN_PITCH
from .keypad import build_keypad, press
from .logic import encode_onehot, any_of

WIDTH = 10
FLAGS = ["CARRY", "ZERO", "NEG", "OVF"]
#: spacer rails between the operand levers and the keypad. The keypad's shared
#: "any key is down" bus runs four blocks past its outermost key, and without a
#: gap it would land on the last operand rail and drive it.
N_GAPS = 3
#: The repeater setting the machine is stable at. Delay 2 burns 117 torches
#: out over fourteen vectors and delay 3 survives gentle sequences but not
#: violent ones; delay 4 is clean on every sequence tried, at 2,584 game ticks
#: from keypress to answer.
DEFAULT_DELAY = 4
#: game ticks between one operand lever moving and the next. Zero means every
#: input changes in the same instant, which no player can do and which the
#: machine does not survive — see `Machine.set_operands`.
SWITCH_SPACING = 2


def build_netlist(width=WIDTH, carry="cla"):
    """Levers and keys in, segment lines and flags out."""
    nl = Netlist()
    A = [nl.input(f"A{i}") for i in range(width)]
    B = [nl.input(f"B{i}") for i in range(width)]
    GAP = [nl.input(f"GAP{i}") for i in range(N_GAPS)]
    K = [nl.input(f"K{k}") for k in range(len(OPS))]

    # the keypad is one-hot; the ALU wants three opcode bits
    OP = encode_onehot(nl, K, 3, name="OP")
    core = alu_core(nl, A, B, OP, carry=carry)

    ndigits = digits_needed(width)
    digits = bin_to_bcd(nl, core["R"], ndigits)
    for k, digit in enumerate(digits):
        # Leading-zero blanking: a digit is shown if it or anything above it is
        # non-zero. That is just an OR over every bit from here up — one gate,
        # flat, no chain — and it folds into the decoder's minterms for free.
        # The units digit is never blanked, so a result of 0 reads as "0".
        show = None if k == 0 else any_of(
            nl, [b for g in digits[k:] for b in g], name=f"SHOW{k}")
        for s, node in seven_seg(nl, digit, enable=show).items():
            nl.output(f"SEG{k}_{s}", node)
    for f in FLAGS:
        nl.output(f, nl.buf(core[f], name=f))

    # Key 0 is ADD, which encodes to all opcode bits low, so on its own it
    # drives nothing and would be optimised away — yet its latch has to exist,
    # because pressing it is what clears the other seven. The spacer rails have
    # no logical purpose at all and would go the same way. One OR keeps every
    # one of them, and lights a lamp once an operation has been chosen.
    nl.output("READY", nl.or_(*K, *GAP))
    nl.align_outputs()
    nl.ndigits = ndigits
    return nl


def build_machine(width=WIDTH, carry="cla", repeater_delay=DEFAULT_DELAY):
    """Place the whole machine. Returns everything a test or a page needs."""
    nl = build_netlist(width, carry)
    ndigits = nl.ndigits
    w = World()
    L = compile_netlist(nl, w, repeater_delay=repeater_delay,
                        drive_inputs=False, fixed_input_order=True)
    (x0, y0, z0), (x1, y1, z1) = w.bounds()

    # ---- player controls ---------------------------------------------------
    # operand bits are levers you flip; the operation is a button you press
    levers = {}
    for i in range(width):
        for pad in ("A", "B"):
            name = f"{pad}{i}"
            rx, ry, rz = L.levers[name]
            w.solid((rx - 2, ry - 1, rz))
            w.lever((rx - 2, ry, rz), attach="down", on=False)
            w.solid((rx - 1, ry - 1, rz))
            w.wire((rx - 1, ry, rz))
            levers[name] = (rx - 2, ry, rz)
    keys = build_keypad(w, [L.levers[f"K{k}"] for k in range(len(OPS))])

    # ---- where the display sits --------------------------------------------
    outs = dict(L.outputs)
    ready = outs.pop("READY")
    seg_out = {k: v for k, v in outs.items() if k.startswith("SEG")}
    flag_out = {f: outs[f] for f in FLAGS}
    y_out = max(p[1] for p in outs.values())
    ya, yb = y_out + 4, y_out + 8

    # Panels read left to right, most significant first. Each one's feed lane
    # arrives as a long X run, and that run has to pass *beneath* the towers of
    # every panel to its left — so feed levels descend as you go right, and the
    # differing drops still land every lamp on one plane.
    panels = [str(k) for k in reversed(range(ndigits))] + ["F"]
    y_disp = {name: yb + 4 * (len(panels) - i) for i, name in enumerate(panels)}
    lamp_y = yb + 4 * (len(panels) + 1)

    nets = []                       # (source, target, panel)
    for k in range(ndigits):
        for s in SEGS:
            nets.append((seg_out[f"SEG{k}_{s}"], (str(k), s)))
    for f in FLAGS:
        nets.append((flag_out[f], ("F", f)))
    # Turn-column order is what makes the loom crossing-free, and it has to run
    # from the highest feed level down. Each net climbs a tower at its own turn
    # column from yb all the way to its panel's feed level, and each net's final
    # run travels east past every *later* turn column. So a run collides with a
    # tower exactly when they share a Z and the run's level is at or below the
    # tower's top. Handing out columns in descending feed level makes every
    # later tower shorter than the run passing over it, and the only nets left
    # sharing a level are the seven segments of one digit, which sit on
    # different Z by construction.
    nets.sort(key=lambda n: (-y_disp[n[1][0]], n[0][2]))
    zs = [n[0][2] for n in nets]
    assert len(set(zs)) == len(zs), "loom nets must leave on distinct Z lanes"
    assert len({n[0][1] for n in nets}) == 1, "outputs must exit on one level"

    disp_x = x1 + 24 + TURN_PITCH * len(nets)
    span = 40
    digits, lamps = {}, {}
    for i, name in enumerate(panels):
        ox = disp_x + span * i
        drop = lamp_y - y_disp[name]
        if name == "F":
            entries, lp = build_lamp_row(w, origin=(ox, lamp_y, 0),
                                         n=len(FLAGS), feed_drop=drop,
                                         lane_len=1)
            digits["F"] = dict(zip(FLAGS, entries))
            lamps["F"] = dict(zip(FLAGS, lp))
        else:
            _, entries, lp = build_digit(w, origin=(ox, lamp_y, 0),
                                         feed_drop=drop, lane_len=1)
            digits[name] = entries
            lamps[name] = lp

    # ---- the loom ----------------------------------------------------------
    stats = {"nets": 0, "towers": 0}
    for i, (src, (panel, line)) in enumerate(nets):
        st = route(w, [(src, digits[panel][line])], ya, yb, y_disp[panel],
                   turn_x0=x1 + 6 + TURN_PITCH * i)
        stats["nets"] += st["nets"]
        stats["towers"] += st["towers"]

    # a lamp beside the READY rail, lit once an operation has been chosen
    rx, ry, rz = ready
    for dx in (1, 2):
        w.solid((rx + dx, ry - 1, rz))
        w.wire((rx + dx, ry, rz))
    w.lamp((rx + 3, ry, rz))
    stats["blocks"] = len(w.blocks)
    stats["gates"] = nl.gate_count()
    stats["depth"] = nl.depth()
    return Machine(nl, w, L, levers, keys, lamps, (rx + 3, ry, rz), stats,
                   width, ndigits)


class Machine:
    """The placed machine, plus the handles needed to operate it."""

    def __init__(self, nl, world, layout, levers, keys, lamps, ready, stats,
                 width, ndigits):
        self.nl = nl
        self.world = world
        self.layout = layout
        self.levers = levers        # {"A3": pos} — operand bits
        self.keys = keys            # {opcode index: button pos}
        self.lamps = lamps          # {"0".."3": {seg: [pos]}, "F": {flag: pos}}
        self.ready = ready
        self.stats = stats
        self.width = width
        self.ndigits = ndigits

    # -- driving it, the way a player does --
    def set_operands(self, engine, a, b, spacing=SWITCH_SPACING):
        """Flip the operand levers to spell out `a` and `b`.

        Only the levers that differ move, and `spacing` game ticks pass between
        one and the next. That is not politeness: a player cannot move twenty
        levers inside a single game tick, and doing so aligns every hazard in
        the machine at the same instant — enough to burn torches out at a delay
        the machine is otherwise perfectly stable at.
        """
        for i in range(self.width):
            for pad, v in (("A", a), ("B", b)):
                pos = self.levers[f"{pad}{i}"]
                want = bool((v >> i) & 1)
                if engine.w.blocks[pos].on != want:
                    engine.set_lever(pos, want)
                    if spacing:
                        engine.run(spacing)

    def press_op(self, engine, op, hold_ticks=40):
        """Hold the key down for as long as a stone button stays pressed, then
        let it go. The caller settles, so the wait can be measured from the
        press rather than from the release."""
        press(engine, self.keys, op, hold_ticks=hold_ticks, settle=False)

    # -- reading it, off the lamps --
    def read_value(self, engine):
        """The number on the digits, or None if any digit is not a numeral."""
        total = 0
        for k in range(self.ndigits):
            d = read_digit(engine, self.lamps[str(k)])
            if d is None:
                # a blank leading digit is a suppressed zero, not a fault
                if lit_segments(engine, self.lamps[str(k)]) != "":
                    return None
                d = 0
            total += d * (10 ** k)
        return total

    def read_flags(self, engine):
        return {f: 1 if engine.read(self.lamps["F"][f]) > 0 else 0
                for f in FLAGS}

    def expect(self, op, a, b):
        r, carry, zero, neg, ovf = reference(op, a, b, self.width)
        return r, {"CARRY": carry, "ZERO": zero, "NEG": neg, "OVF": ovf}
