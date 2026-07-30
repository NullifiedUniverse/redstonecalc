"""A control panel you can operate standing still.

The compiler puts input rails on a four-block Z pitch, so twenty operand bits
land spread over eighty blocks, and the keypad sits at the rail plane a dozen
blocks above them. Measured with `tools/ergonomics.py`, entering both operands
that way costs ten standing spots and 115 blocks of walking, and eight of the
twenty-eight controls are not on the same wall as the rest — the machine
computes fine and is miserable to use.

This puts every control where a player wants it instead: two rows on a
two-block pitch, one wall, and one route per net from the wall to whatever the
control has to drive.

The routing is crossing-free by construction rather than by search. Each net
takes the same four legs, and each leg is on a coordinate nothing else shares:

    lever ─▶ west along X at (its row's height, its own panel Z)
          ─▶ up a tower at its own turn column
          ─▶ along Z at that turn column to the target's Z
          ─▶ up, then east at one shared height, on the target's own Z
          ─▶ up into the block the target reads

Panel rows get different heights, so the two controls that share a panel Z never
share a lane. Turn columns are one per net, handed out in index order, and
within a column the lower row comes second — so its taller tower stands west of
where the upper row's run ever reaches. The last two legs live on the target's
own Z, which is unique because two controls cannot drive the same cell. Nothing
has to be searched, and `test_panel_routes_never_cross` checks the claim by
watching every write.

Everything runs *west*, away from the machine. The lever's own dust is on its
west side for the same reason: a run that started east of the lever would have
to come back over it, which is exactly the bug this layout is written to make
impossible.
"""

from __future__ import annotations

from .engine import World
from .harness import tower, line

#: The panel sits below the input rails, far enough west of them that its
#: routing never meets the keypad's own wiring.
PANEL_X = -20           # the wall the player stands at
TURN_X0 = -24           # first turn column; one per net, further out each time
TURN_PITCH = -2
PANEL_PITCH = 2         # Z spacing between adjacent controls on a row
#: heights, all four apart so every torch tower is non-inverting. Row 0 is the
#: upper one; see the module docstring for why the order matters.
Y_ROW = (-12, -16)      # one per panel row
Y_CROSS = -8            # the Z-crossing level
Y_APPROACH = -4         # the shared run in to the targets
WALK_X = (PANEL_X + 1, PANEL_X + 2)   # the floor the player stands on
WALK_MARGIN = 2         # how far it runs past the end control


def build_control_panel(w: World, targets, rows=2, origin_z=0):
    """Wire a compact lever panel to `targets`, in order.

    Each target is the cell a net has to *drive*: the panel finishes with a
    torch tower whose top block lands exactly there, strongly powered whenever
    that net's lever is on. What reads it is the caller's business — a dust cell
    beside an input rail, or the block a keypad's button used to be.

    Returns the lever positions in the same order.

    Control *i* goes on row ``i % rows``, column ``i // rows``, so consecutive
    targets stack in one column: pass A and B interleaved and a player setting
    bit 3 of both touches two levers four blocks apart instead of walking forty.
    """
    assert rows <= len(Y_ROW), f"only {len(Y_ROW)} panel rows are laid out"
    seen = set()
    for t in targets:
        assert t not in seen, f"two controls drive {t}"
        seen.add(t)
    levers = [route_control(w, i, t, rows, origin_z)
              for i, t in enumerate(targets)]
    build_walkway(w, levers)
    return levers


def build_walkway(w: World, levers):
    """A floor to stand on, level with the lowest row.

    Not decoration: the reach figures in `tools/ergonomics.py` are measured
    from a player's eyes standing here, and without a floor there is nowhere to
    stand that reaches both rows. Feet land level with the bottom row and the
    top row sits four blocks up, which from eye height is a 2.6-block reach —
    so one walkway serves the whole wall and nothing has to be climbed.

    Glass, deliberately: it is not conductive in this model or in the game, so a
    walkway cannot carry power into the panel however it is extended.
    """
    zs = [p[2] for p in levers]
    floor = min(p[1] for p in levers) - 1
    for x in WALK_X:
        for z in range(min(zs) - WALK_MARGIN, max(zs) + WALK_MARGIN + 1):
            w.solid((x, floor, z), kind="glass")
    return floor + 1


def route_control(w: World, i, target, rows=2, origin_z=0):
    """Place control `i` and its route. Returns the lever position.

    Split out from the loop so a test can watch one net at a time — see
    `test_panel_routes_never_cross`, which is what keeps the docstring's
    crossing-free claim honest.
    """
    tx, ty, tz = target
    py = Y_ROW[i % rows]
    pz = origin_z + PANEL_PITCH * (i // rows)
    turn = TURN_X0 + TURN_PITCH * i

    # --- the lever, and the dust it feeds, both on the west side
    w.solid((PANEL_X, py - 1, pz))
    w.lever((PANEL_X, py, pz), attach="down", on=False)
    w.solid((PANEL_X - 1, py - 1, pz))
    w.wire((PANEL_X - 1, py, pz))

    # --- along the row's own height, out to this net's turn column
    line(w, PANEL_X - 2, turn + 1, py, pz, "x", lead=True)

    # --- up, across in Z, up again
    gap = abs(tz - pz)
    assert gap % 2 == 0, (
        f"panel Z {pz} and target Z {tz} have different parity: a one-cell "
        f"gap leaves no room for the dust between two towers")
    if gap == 0:
        tower(w, turn, py, pz, Y_APPROACH - py)
    else:
        tower(w, turn, py, pz, Y_CROSS - py)
        zstep = 1 if tz > pz else -1
        line(w, pz + zstep, tz - zstep, Y_CROSS, turn, "z", lead=True,
             step=zstep)
        tower(w, turn, Y_CROSS, tz, Y_APPROACH - Y_CROSS)

    # --- in on the shared approach level, then up into the target
    line(w, turn + 1, tx - 1, Y_APPROACH, tz, "x", lead=True)
    tower(w, tx, Y_APPROACH, tz, ty - Y_APPROACH)
    return (PANEL_X, py, pz)


def panel_extent(levers):
    """(width along Z, height along Y) of the finished panel."""
    zs = [p[2] for p in levers]
    ys = [p[1] for p in levers]
    return max(zs) - min(zs) + 1, max(ys) - min(ys) + 1
