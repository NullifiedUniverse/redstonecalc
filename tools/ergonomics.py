"""How much work is it to actually use this machine in Minecraft?

Correctness tests say the machine computes; they say nothing about whether a
player can operate it without walking a hundred blocks. This measures the part
the tests miss, in the units that matter in game:

  * how many clicks a task takes,
  * how far you walk between them, and how far you have to climb,
  * how far you can reach without moving (about 4.5 blocks in Java Edition,
    from the eyes),
  * how many separate standing spots the task needs,
  * and how far the answer is from where you entered it.

Run:  python3 tools/ergonomics.py [--compare]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.machine import build_machine
from rscalc.alu import OPS

#: A player's block-interaction reach in Java Edition, in blocks, measured from
#: the eyes — not from the feet, which is what makes a two-row wall work at all.
REACH = 4.5
#: eye height above the block a player stands on
EYE = 1.62


def reaches(spot, control):
    """Can a player standing on `spot` click `control` without moving?

    Both are block coordinates; the player's eyes are EYE above the top of the
    block they stand on, and Minecraft measures reach to the target block's
    centre.
    """
    ex, ey, ez = spot[0] + 0.5, spot[1] + EYE, spot[2] + 0.5
    cx, cy, cz = control[0] + 0.5, control[1] + 0.5, control[2] + 0.5
    return ((ex - cx) ** 2 + (ey - cy) ** 2 + (ez - cz) ** 2) ** 0.5 <= REACH


def candidates(controls):
    """Every block a player might stand on to work these controls.

    One step either side of each control's own X, at every height and Z the
    controls span. Whether a floor is actually there is the build's problem —
    `rscalc.panel` puts one in, which is why the panel needs no climbing.
    """
    xs = {p[0] + d for p in controls for d in (1, -1)} - {p[0] for p in controls}
    ys = range(min(p[1] for p in controls) - 1, max(p[1] for p in controls) + 2)
    zs = range(min(p[2] for p in controls), max(p[2] for p in controls) + 1)
    return [(x, y, z) for x in sorted(xs) for y in ys for z in zs]


def spots_needed(controls):
    """Fewest standing positions that reach every control, greedily.

    Greedy set cover: repeatedly take the spot that reaches the most controls
    nobody has reached yet. Not provably minimal, but it is choosing from every
    plausible place to stand rather than assuming the player stands on top of
    the lever, which is what the first version of this did — and that version
    charged the two-row panel 24 blocks of climbing it does not need.
    """
    left, spots = list(controls), []
    pool = candidates(controls)
    while left:
        best = max(pool, key=lambda s: sum(reaches(s, c) for c in left))
        got = [c for c in left if reaches(best, c)]
        assert got, f"no reachable spot for {left[0]}"
        spots.append(best)
        left = [c for c in left if c not in got]
    # visit them in the order you meet them walking the wall
    return sorted(spots, key=lambda s: (s[2], s[0], s[1]))


def walk(spots):
    """Distance travelled visiting the standing spots in order.

    Horizontal and vertical are counted separately, because in Minecraft they
    are not the same cost: you stroll along a wall, and you climb a ladder.
    """
    flat = climb = 0.0
    for a, b in zip(spots, spots[1:]):
        flat += ((a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
        climb += abs(a[1] - b[1])
    return flat, climb


def report(m, label):
    ops = [m.keys[k] for k in range(len(OPS))]
    operands = [m.levers[f"{pad}{i}"]
                for i in range(m.width) for pad in ("A", "B")]
    everything = operands + ops

    zs = [p[2] for p in everything]
    ys = [p[1] for p in everything]
    span = max(zs) - min(zs) + 1
    height = max(ys) - min(ys) + 1

    # A worst-case entry: every operand bit changes, then one operation.
    spots = spots_needed(everything)

    # A realistic edit: change three bits and press one operation
    edit = [m.levers["A0"], m.levers["A3"], m.levers["B1"], ops[1]]
    edit_spots = spots_needed(edit)

    # …and then you have to go and look at the answer.
    digits = [p for d in range(m.ndigits)
              for seg in m.lamps[str(d)].values() for p in seg]
    lamp = min(digits, key=lambda p: (p[0], p[2]))
    to_read = walk([spots[-1], lamp])

    flat, climb = walk(spots)
    eflat, eclimb = walk(edit_spots)
    print(f"{label}")
    print(f"  controls              {len(everything)} "
          f"({len(operands)} operand levers, {len(ops)} operation keys)")
    print(f"  they span             {span} blocks along Z, "
          f"{height} along Y")
    print(f"  full entry            {len(operands) + 1} clicks, "
          f"{len(spots)} standing spots, "
          f"{flat:.0f} blocks walked + {climb:.0f} climbed")
    print(f"  three-bit edit        {len(edit)} clicks, "
          f"{len(edit_spots)} standing spots, "
          f"{eflat:.0f} blocks walked + {eclimb:.0f} climbed")
    print(f"  then read the answer  {to_read[0]:.0f} blocks away, "
          f"{to_read[1]:.0f} up")
    return {"span": span, "height": height, "spots": len(spots),
            "walk": flat, "climb": climb, "edit_spots": len(edit_spots),
            "edit_walk": eflat, "edit_climb": eclimb,
            "read_walk": to_read[0], "read_climb": to_read[1]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--compare", action="store_true",
                    help="also measure the layout the compiler leaves behind")
    args = ap.parse_args()
    kw = {"width": args.width} if args.width else {}
    m = build_machine(**kw)
    report(m, f"Mk III, {m.width}-bit, one control wall")
    if args.compare:
        print()
        old = build_machine(compact_input=False, **kw)
        report(old, f"Mk III, {old.width}-bit, controls left on the rails")
