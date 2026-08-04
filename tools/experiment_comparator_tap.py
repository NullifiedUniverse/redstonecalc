"""Why the tap keeps its torch: the comparator inverter, measured.

A redstone torch is the only single block that inverts, and it is the only block
in this machine that can *die* — forced off more than eight times in sixty game
ticks and it burns out for good (§13). A comparator in subtract mode with a
redstone block on its back inverts too, and has no burnout rule at all, so it
looks like the structural fix.

It is not, and this file is why. Three measurements, in order:

  1. the comparator inverter works, and survives a hammering that kills a torch;
  2. its "off" is only off when the side input is *exactly* 15 — it subtracts a
     level, not a boolean, so the tap would need a repeater to restore the rail
     before the comparator could read it;
  3. that repeater has nowhere to put its output. A strongly powered block
     drives every dust beside it to a full 15, and the only cell in the tap's
     envelope a repeater reading the rail can reach is the cell touching the
     rail.

Run: python3 tools/experiment_comparator_tap.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine

LEVER = (0, 0, -3)
COMP = (0, 0, 2)
OUT = (-1, 0, 2)


def floor(w, xs, zs):
    for x in xs:
        for z in zs:
            w.solid((x, -1, z))


def inverter(side_repeater=True, side_len=1):
    """A comparator inverting whatever arrives at its north side.

    With `side_repeater` the side is fed by a repeater, so it is exactly 15 or
    exactly 0. Without it the side is fed by `side_len` cells of dust, which is
    how a tap would actually reach it, and the level that arrives is whatever
    the run has left.
    """
    w = World()
    floor(w, range(-3, 3), range(-16, 4))
    w.comparator(COMP, facing="west", mode="subtract")
    w.redstone_block((1, 0, 2))                 # a constant 15 on the back
    w.wire(OUT)
    w.wire((-2, 0, 2))                          # what the comparator drives
    if side_repeater:
        w.repeater((0, 0, 1), facing="south", delay=1)
        w.wire((0, 0, 0))
        w.wire((0, 0, -1))
        w.wire((0, 0, -2))
        w.lever(LEVER, attach="down", on=False)
    elif side_len:
        for z in range(1 - side_len + 1, 2):
            w.wire((0, 0, z))
        w.lever((0, 0, 1 - side_len), attach="down", on=True)
    else:
        w.wire((0, 0, 1))                       # a side with nothing driving it
    assert not w.lint(), w.lint()[:3]
    return w


def measure_inverter():
    print("1. does it invert, and does it survive?")
    w = inverter()
    e = Engine(w)
    e.initialize_steady()
    side = (0, 0, 1)

    rest = e.read(OUT)
    t = e.now
    e.set_lever(LEVER, True)
    e.run_until_stable(200)
    rise, hi = e.now - t, e.read(OUT)
    t = e.now
    e.set_lever(LEVER, False)
    e.run_until_stable(200)
    fall = e.now - t
    print(f"   input off -> out {rest:2}      input on -> out {hi:2}"
          f"      ({rise} gt up, {fall} gt down)")

    # a torch dies after eight state changes in sixty game ticks; this is sixty
    # changes three game ticks apart, which is far past that
    for i in range(60):
        e.set_lever(LEVER, i % 2 == 0)
        e.run(3)
    e.run_until_stable(400)
    e.set_lever(LEVER, False)
    e.run_until_stable(200)
    print(f"   after 60 flips 3 gt apart: {len(e.burned_out)} burned out, "
          f"still reads out={e.read(OUT)} with the input off")
    print("   So the inversion is real and it cannot burn out. That much is "
          "worth having.")
    return rest, hi


def measure_side_levels():
    """Feed the side every level 0..15 and read what comes out the front."""
    print("\n2. 'off' costs a full 15: out = 15 - side, so a weak side is on")
    rows = []
    for level in range(16):
        w = inverter(side_repeater=False, side_len=16 - level)
        e = Engine(w)
        e.initialize_steady()
        rows.append((level, e.read((0, 0, 1)), e.read(OUT)))
    for level, got, _ in rows:
        assert got == level, f"wanted a side of {level}, built {got}"
    print("   side  " + " ".join(f"{s:2}" for s, _, _ in rows))
    print("   out   " + " ".join(f"{o:2}" for _, _, o in rows))
    live = sorted(s for s, _, o in rows if o > 0 and s > 0)
    print(f"   A side of {live[0]}..{live[-1]} still leaves the output on. "
          "Only exactly 15 turns it off,")
    print("   and a rail carries whatever its last repeater left it — 2 at "
          "worst, by design.")
    print("   So a comparator tap needs a repeater of its own to restore the "
          "rail first.")
    return rows


def measure_strong_block_beside_a_rail():
    """That restoring repeater has to strongly power a block. Where?

    A comparator's side reads only the cell beside it, and only at 15, so the
    block the restoring repeater powers has to have dust on it *in that cell*.
    Work backwards from there through the tap's three cells of Z and the block
    lands beside the rail. Put it there and see what the rail does.
    """
    print("\n3. where that repeater cannot go: beside the rail")
    w = World()
    floor(w, range(-4, 4), range(-1, 4))
    w.lever((-4, 0, 0), attach="down", on=False)   # the rail is OFF throughout
    for x in range(-3, 3):
        w.wire((x, 0, 0))                          # the rail
    w.solid((1, 0, 1))                             # the strongly powered block
    w.repeater((1, 0, 2), facing="north", delay=1)
    w.lever((1, 0, 3), attach="down", on=True)     # stands in for another tap
    assert not w.lint(), w.lint()[:3]
    e = Engine(w)
    e.initialize_steady()
    leaked = e.read((1, 0, 0))
    print(f"   rail lever OFF, a strongly powered block one cell of Z away: "
          f"the rail reads {leaked}")
    print("   Strong power reaches adjacent dust at a full 15 (§2), so the "
          "restoring repeater's")
    print("   output block may never sit next to the rail.")
    return leaked


def main():
    rest, hi = measure_inverter()
    rows = measure_side_levels()
    leaked = measure_strong_block_beside_a_rail()
    print("""
conclusion
  The comparator inverts, and it cannot burn out. Paying for it costs a
  restoring repeater, the block that repeater powers, a comparator and a
  redstone block, where a torch costs a stub, a block, itself and a block.
  At the pitch this was measured on — RAIL_PITCH = GATE_PITCH = 4 — the tap gets
  three cells of Z and three of X, and no arrangement of those four keeps the
  strongly powered block off the rail *and* every dust off the collector: the
  rail bounds the envelope on one side and the collector bounds it on the other.
  Widening the lattice to fit it grows the machine's longest axis by half, which
  lengthens every collector crossing that axis, which adds back the repeaters
  the change was meant to remove. GATE_PITCH is 3 now (DESIGN §18), which leaves
  the envelope a column narrower still.

  So the tap keeps its torch. The cheaper version of the same idea — a repeater
  in the stub's cell, which costs no space at all — was measured too, and lost
  its own A/B: see §17 and `tools/experiment_hostile_inputs.py`.""")
    assert rest > 0 and hi == 0, "the comparator is supposed to invert"
    assert any(o > 0 for s, _, o in rows if 0 < s < 15), "side must be 15"
    assert leaked == 15, "a strong block beside a rail should drive it to 15"


if __name__ == "__main__":
    main()
