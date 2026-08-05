"""Mk III — a 10-bit calculator, complete, in one world.

    28 levers on one wall  ->  A, B, and the operation
    the 8 operation levers ->  one-hot latches -> 3-bit opcode
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
from .panel import build_control_panel, PANEL_PITCH

WIDTH = 10
FLAGS = ["CARRY", "ZERO", "NEG", "OVF"]
#: spacer rails between the operand levers and the keypad. The keypad's shared
#: "any key is down" bus runs four blocks past its outermost key, and without a
#: gap it would land on the last operand rail and drive it.
N_GAPS = 3
#: The repeater setting the machine is stable at. Every setting below it burns
#: torches out and gets the wrong answer; this one is clean on every sequence
#: tried, including flipping every lever in the same game tick. The margin moves
#: whenever the layout does — §13's table has been re-measured twice for exactly
#: that reason — so the numbers live there and not here, where they would go
#: quietly stale. `tools/verify_machine.py --delay 2 --delay 3 --delay 4`
#: reproduces it in about six minutes.
DEFAULT_DELAY = 4
#: worst settle measured over the 300 vectors in `tools/verify_full.py` at
#: DEFAULT_DELAY — from the operation lever going down to the last lamp holding
#: still, just under two minutes in game. Quoted in the build output's README so
#: a builder knows what they are waiting for. It was 2,644 before §18 took the
#: Z ratchet out and a quarter of the gate pitch with it.
SETTLE_GT = 2352
#: X between one display panel and the next. A digit is six blocks wide and a
#: flag row two, so this was 40 for a footprint of 6 — the panels sat in 170
#: blocks of air. What it actually has to clear is each panel's feed lane and
#: the run coming in past its neighbours, which is why it is measured (§19)
#: rather than guessed at.
PANEL_SPAN = 8
#: game ticks between one operand lever moving and the next. Zero means every
#: input changes in the same instant, which no player can do and which the
#: machine does not survive — see `Machine.set_operands`.
SWITCH_SPACING = 2


def value_of(digits):
    """Assemble a decimal number from lamp groups, **least significant first**.

    Lamp group `"0"` is the units, `"1"` the tens, and so on, and that ordering
    is a convention shared by the placer, the exporter, this reader and the
    page — reverse it and every answer comes back with its digits mirrored, so
    999 still reads 999 and 723 reads 327. It is a one-line function so that it
    can be tested without standing up half a million blocks first: it lived
    inside `Machine.read_value`, and `tools/mutate_core.py` reversed it there
    with the whole fast suite still green.
    """
    return sum(d * 10 ** k for k, d in enumerate(digits))


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


def build_machine(width=WIDTH, carry="cla", repeater_delay=DEFAULT_DELAY,
                  compact_input=True):
    """Place the whole machine. Returns everything a test or a page needs.

    `compact_input` routes all 28 controls — the operand levers and the eight
    operation keys — onto one wall a player can work standing still, instead of
    leaving the levers spread along eighty blocks of rail with the keypad on a
    different level. Measured with `tools/ergonomics.py`; pass False for the
    layout the compiler leaves behind, which is what the measurement compares
    against.
    """
    nl = build_netlist(width, carry)
    ndigits = nl.ndigits
    w = World()
    # `alternate` is the §12 fix, and thirty-six stages is where it pays: it
    # takes the machine from 1,825 blocks deep to 973 and takes 90 game ticks
    # off the answer with it. Shallower builds are better off without it — §18.
    L = compile_netlist(nl, w, repeater_delay=repeater_delay,
                        drive_inputs=False, fixed_input_order=True,
                        alternate=True)
    (x0, y0, z0), (x1, y1, z1) = w.bounds()

    # ---- player controls ---------------------------------------------------
    # All 28 are levers. The operation ones are flipped on and then off again,
    # the way you would press a button: the keypad latches on the way down and
    # holds the choice after the lever falls back.
    names = [f"{pad}{i}" for i in range(width) for pad in ("A", "B")]
    key_rails = [L.levers[f"K{k}"] for k in range(len(OPS))]
    if compact_input:
        # Every control on one wall. The operation keys come first, so they sit
        # beside the low-order bits — the ones an operator actually edits —
        # and the operand bits follow interleaved, A above B in one column each.
        feeds = build_keypad(w, key_rails, remote=True)
        controls = ([(f"OP{k}", feeds[k]) for k in range(len(OPS))]
                    + [(n, (L.levers[n][0] - 2,) + tuple(L.levers[n][1:]))
                       for n in names])
        rows = 2
        # centre the panel on the keypad's Z, on an even lane so no net's Z-run
        # ends up one cell from its target (two towers need dust between them)
        kzs = [t[2] for t in key_rails]
        span = PANEL_PITCH * (-(-len(controls) // rows) - 1)
        origin_z = (min(kzs) + max(kzs)) // 2 - span // 2
        placed = build_control_panel(w, [c[1] for c in controls], rows=rows,
                                     origin_z=origin_z - origin_z % 2)
        at = dict(zip([c[0] for c in controls], placed))
        levers = {n: at[n] for n in names}
        keys = {k: at[f"OP{k}"] for k in range(len(OPS))}
        # the dust each operand rail reads, off the top of its panel tower
        for name in names:
            rx, ry, rz = L.levers[name]
            w.solid((rx - 1, ry - 1, rz))
            w.wire((rx - 1, ry, rz))
    else:
        levers = {}
        for name in names:
            rx, ry, rz = L.levers[name]
            w.solid((rx - 2, ry - 1, rz))
            w.lever((rx - 2, ry, rz), attach="down", on=False)
            w.solid((rx - 1, ry - 1, rz))
            w.wire((rx - 1, ry, rz))
            levers[name] = (rx - 2, ry, rz)
        keys = build_keypad(w, key_rails)

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
    span = PANEL_SPAN
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
        digits = []
        for k in range(self.ndigits):
            d = read_digit(engine, self.lamps[str(k)])
            if d is None:
                # a blank leading digit is a suppressed zero, not a fault
                if lit_segments(engine, self.lamps[str(k)]) != "":
                    return None
                d = 0
            digits.append(d)
        return value_of(digits)

    def read_flags(self, engine):
        return {f: 1 if engine.read(self.lamps["F"][f]) > 0 else 0
                for f in FLAGS}

    def expect(self, op, a, b):
        r, carry, zero, neg, ovf = reference(op, a, b, self.width)
        return r, {"CARRY": carry, "ZERO": zero, "NEG": neg, "OVF": ovf}
