"""A ten-button keypad that latches one-hot, in pure redstone.

Pressing a key has to be *remembered* after the button pops back out, so each
key drives a latch. The latches are repeater locks rather than torch pairs:
they hold state with no torch anywhere in the cell, which means they cannot
burn out, and a locked repeater simply ignores its input.

One shared signal does the selecting. Every button feeds a common **any** bus,
and each latch's lock is that bus inverted — so while no key is down, all ten
latches hold, and the moment a key goes down they *all* go transparent at once.
The pressed key's latch then sees a 1 and the other nine see a 0, which is
exactly a one-hot register. No reset logic and no cross-coupling required.

The one subtlety is a race. Releasing a key drops its data and re-asserts the
lock at the same moment, and the data wins — the latch would store 0 and the
display would blank the instant you let go. The fix is to make the data path
deliberately the slower of the two: the lock travels straight through a single
torch, while the data crawls through a chain of repeaters, so the latch is
always shut before its input falls away.

Layout per key, at the target's Z::

    x:  -15   -14   -13 .. -8      -7 .. -3    -2      -1     0
        bus   BUTTON  delay chain    dust     LATCH   dust   -> rail
                                             (locked from Z+1)
"""

from __future__ import annotations

from .engine import World

BUS_X = -15         # the shared "any key is down" bus
BUTTON_X = -14
DELAY_REPEATERS = 3   # each is delay-4, so 24 gt: longer than any lock path
DELAY_SETTING = 4
CHAIN_EVERY = 11      # a dust run cannot cross the whole keypad unaided
LATCH_X = -2
BUS_Y = -2           # the inverted-lock distribution bus runs below


def build_keypad(w: World, targets, name="K"):
    """Wire N buttons to N one-hot outputs, each feeding one target.

    `targets` is the list of positions the keypad must drive, in key order —
    they are the input rails of whatever reads the keypad. Returns the button
    positions, keyed by index. Ten keys makes a digit pad; eight makes an
    operation selector; the mechanism does not care.
    """
    assert len(targets) >= 2, "a one-hot pad needs at least two keys"
    # Measured working range: 6, 8, 10 and 12 keys all latch one-hot with no
    # burnout. At 16 the shared bus no longer reaches the far keys and they stop
    # latching, so the limit is asserted rather than left to fail quietly — a
    # wider pad needs a second repeater stage on the distribution chain.
    assert len(targets) <= 12, (
        f"{len(targets)} keys: the shared bus is only verified to 12")
    zs = [t[2] for t in targets]
    y = targets[0][1]
    buttons = {}

    def floor_dust(p):
        w.solid((p[0], p[1] - 1, p[2]))
        w.wire(p)

    for key, (tx, ty, tz) in enumerate(targets):
        # data path: button -> repeater chain -> latch -> target
        w.solid((BUTTON_X, ty - 1, tz))
        w.lever((BUTTON_X, ty, tz), attach="down", on=False)
        buttons[key] = (BUTTON_X, ty, tz)

        x = BUTTON_X + 1
        for _ in range(DELAY_REPEATERS):
            floor_dust((x, ty, tz))
            w.solid((x + 1, ty - 1, tz))
            w.repeater((x + 1, ty, tz), facing="east", delay=DELAY_SETTING)
            x += 2
        while x < LATCH_X:
            floor_dust((x, ty, tz))
            x += 1

        w.solid((LATCH_X, ty - 1, tz))
        w.repeater((LATCH_X, ty, tz), facing="east", delay=1)   # the latch
        floor_dust((LATCH_X + 1, ty, tz))                       # feeds the rail

        # lock: a repeater pointing into the latch's side freezes it
        w.solid((LATCH_X, ty - 1, tz + 1))
        w.repeater((LATCH_X, ty, tz + 1), facing="north", delay=1)

        # the lock is driven by one torch inverting the shared bus, so the
        # latch is held exactly while no key is down
        w.solid((LATCH_X, ty, tz + 2))                # strongly powered below
        w.solid((LATCH_X, ty - 2, tz + 2))            # torch support
        w.torch((LATCH_X, ty - 1, tz + 2), attach="down")
        # a stub, not the bus itself: a straight run of dust points along its
        # own axis, so it would never power a block off to the side
        w.solid((LATCH_X - 1, BUS_Y - 1, tz + 2))
        w.wire((LATCH_X - 1, BUS_Y, tz + 2))

        # every button also feeds the shared bus
        w.solid((BUS_X, ty - 1, tz))
        w.wire((BUS_X, ty, tz))

    # The "any key is down" signal has to gather from every key and then reach
    # every lock, and the keypad is far longer than dust can carry. So it runs
    # as two repeater chains: collect toward +Z, then distribute back along -Z
    # underneath. Repeaters are one-way, which is exactly right here — keys only
    # ever feed the chain, and the chain only ever feeds locks.
    lo, hi = min(zs) - 4, max(zs) + 3
    _chain(w, BUS_X, y, lo, hi, "south", skip=set(zs))
    # step it down two levels, clear of the key rows
    w.solid((BUS_X + 1, BUS_Y, hi))
    w.wire((BUS_X + 1, BUS_Y + 1, hi))
    w.solid((BUS_X + 2, BUS_Y - 1, hi))
    w.wire((BUS_X + 2, BUS_Y, hi))
    # the collected signal arrives already faded, so restore it before the
    # crossing run rather than letting it die on the way
    w.solid((BUS_X + 3, BUS_Y - 1, hi))
    w.repeater((BUS_X + 3, BUS_Y, hi), facing="east", delay=1)
    for x in range(BUS_X + 4, LATCH_X - 2):
        w.solid((x, BUS_Y - 1, hi))
        w.wire((x, BUS_Y, hi))
    # the distribution chain opens with a repeater: the collected signal
    # arrives faded, and would die before reaching the chain's first one
    _chain(w, LATCH_X - 2, BUS_Y, lo, hi, "north",
           skip={z + 2 for z in zs}, lead=True)
    return buttons


def _chain(w: World, x, y, z_lo, z_hi, facing, skip=(), lead=False):
    """Dust along Z with repeaters, so the run can cross the whole keypad."""
    cells = list(range(z_lo, z_hi + 1))
    if facing == "north":
        cells.reverse()

    # decide where the repeaters go before placing anything
    rep, since = set(), 0
    for i, z in enumerate(cells):
        if i == 0 and lead:
            # the feed arrives from the side, which a repeater ignores, so the
            # chain opens on dust and repeats at the very next chance
            since = CHAIN_EVERY
            continue
        if since >= CHAIN_EVERY and z not in skip:
            rep.add(z)
            since = 0
        else:
            since += 1

    # The chain has to hand its signal sideways and downward to the crossing,
    # and a repeater only ever feeds the single block in front of it — so the
    # last cell must be plain dust. Where the spacing wanted a repeater there,
    # it moves back one cell instead of being dropped, which would leave the
    # tail of the run to fade out.
    if cells and cells[-1] in rep:
        rep.discard(cells[-1])
        if len(cells) > 1 and cells[-2] not in skip:
            rep.add(cells[-2])

    for z in cells:
        w.solid((x, y - 1, z))
        if z in rep:
            w.repeater((x, y, z), facing=facing, delay=1)
        else:
            w.wire((x, y, z))


def press(engine, buttons, key, hold_ticks=4, settle=True):
    """Press and release a key, the way a player would."""
    engine.set_lever(buttons[key], True)
    engine.run(hold_ticks)
    engine.set_lever(buttons[key], False)
    if settle:
        return engine.run_until_stable(4000)
    return 0
