"""Drive the whole calculator hard, through nothing but the player's controls.

`verify_machine.py` runs a handful of vectors. This is the wider sweep: every
operation over its own boundary cases plus a random sample, every operation
tried directly after every other operation so nothing is left latched, the
digits read off the lamps, the flags read off the flag row, and the torch count
checked at the end. Nothing here reaches inside the machine — operands go in on
the wall levers, the operation on its key, and the answer comes off the lamps.

    python3 tools/verify_full.py                 # ~200 vectors
    python3 tools/verify_full.py --vectors 400
    python3 tools/verify_full.py --delay 3 --vectors 60
"""

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import Engine
from rscalc.machine import build_machine, FLAGS, DEFAULT_DELAY
from rscalc.alu import OPS
from rscalc.steady import settled_engine

#: the values that break arithmetic if anything is going to break it
EDGES = [0, 1, 2, 511, 512, 513, 1022, 1023]


def vectors(width, n, seed):
    """Boundaries first, then every ordered pair of operations, then random.

    `n` is a floor, not a cap: the structured part is what makes this worth
    running and truncating it to hit a round number would quietly drop the very
    cases it exists for. Asking for fewer than the structured set gets the
    structured set, and `main` prints how many it is actually driving.
    """
    hi = (1 << width) - 1
    out = []
    for op in range(len(OPS)):
        for a in (0, 1, hi, hi - 1, 512):
            for b in (0, 1, hi, 512):
                out.append((op, a, b))
    # every operation directly after every other one, since the machine holds
    # its opcode in a latch and a stale one is exactly the fault a single-shot
    # sweep cannot see
    rng = random.Random(seed)
    for prev in range(len(OPS)):
        for nxt in range(len(OPS)):
            out.append((prev, rng.choice(EDGES), rng.choice(EDGES)))
            out.append((nxt, rng.choice(EDGES), rng.choice(EDGES)))
    while len(out) < n:
        out.append((rng.randrange(len(OPS)), rng.randrange(1 << width),
                    rng.randrange(1 << width)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=int, default=DEFAULT_DELAY)
    ap.add_argument("--vectors", type=int, default=200,
                    help="a floor, not a cap — see `vectors`")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    m = build_machine(repeater_delay=args.delay)
    problems = m.world.lint()
    assert not problems, problems[:4]
    e = settled_engine(m.world, Engine, use_cache=True, verify=True)
    cases = vectors(m.width, args.vectors, args.seed)
    (x0, y0, z0), (x1, y1, z1) = m.world.bounds()
    print(f"Mk III at delay {args.delay}: {len(m.world.blocks):,} blocks, "
          f"{x1-x0+1}x{y1-y0+1}x{z1-z0+1}, {m.stats['gates']:,} gates, "
          f"depth {m.stats['depth']}, lint {len(problems)}")
    print(f"driving {len(cases)} vectors through the wall levers "
          f"({len(EDGES)} boundary values, every operation after every other)")

    bad, worst, t1 = [], 0, time.time()
    for n, (op, a, b) in enumerate(cases):
        m.set_operands(e, a, b)
        t = e.now
        m.press_op(e, op)
        e.run_until_stable(400000)
        worst = max(worst, e.now - t)
        got, flags = m.read_value(e), m.read_flags(e)
        want, wflags = m.expect(op, a, b)
        if got != want or flags != wflags:
            bad.append((op, a, b, got, want, flags, wflags))
        if not args.quiet and (n + 1) % 25 == 0:
            print(f"  {n+1:>4}/{len(cases)}  {len(bad)} wrong, "
                  f"{len(e.burned_out)} burned, worst {worst} gt",
                  flush=True)

    ok = not bad and not e.burned_out
    print(f"\n{len(cases)-len(bad)}/{len(cases)} correct including flags, "
          f"{len(e.burned_out)} torches burned out")
    print(f"worst settle {worst} gt ({worst/20:.0f} s in game); "
          f"build+settle {t1-t0:.0f}s, drive {time.time()-t1:.0f}s")
    for op, a, b, got, want, flags, wflags in bad[:6]:
        print(f"  {OPS[op]} {a},{b}: got {got} want {want}; "
              f"flags {''.join(str(flags[f]) for f in FLAGS)} "
              f"want {''.join(str(wflags[f]) for f in FLAGS)}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
