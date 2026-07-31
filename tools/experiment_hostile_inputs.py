"""How fast can the inputs move before the machine breaks? — measured.

§13 measured this once and its conclusion — delay 4, and inputs that arrive at
human speed — is what the machine ships at. This is that measurement, kept
runnable, because every later question about the tap (§17) was settled by
running it again rather than by arguing.

The hostile part is the spacing: operand pairs chosen to move ten or more levers,
flipped `spacing` game ticks apart. At zero every hazard in the machine fires in
the same instant, which no player can do — it is here as the worst case, not as
a realistic one. Seeds matter more than spacing does, which is why there is a
`--seed` and why §17's table quotes three of them.

    python3 tools/experiment_hostile_inputs.py --delay 3 --seed 11 --seed 23
"""

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import Engine
from rscalc.machine import build_machine
from rscalc.alu import OPS
from rscalc.steady import settled_engine


def hostile_pairs(n, width, seed=11, min_moves=10):
    """Operand pairs that move at least `min_moves` levers from the last one."""
    rng = random.Random(seed)
    out, cur = [], (0, 0)
    while len(out) < n:
        a, b = rng.randrange(1 << width), rng.randrange(1 << width)
        moved = bin(a ^ cur[0]).count("1") + bin(b ^ cur[1]).count("1")
        if moved >= min_moves:
            out.append((rng.randrange(len(OPS)), a, b))
            cur = (a, b)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=int, default=3)
    ap.add_argument("--vectors", type=int, default=10)
    ap.add_argument("--spacings", default="0,1,2,4")
    ap.add_argument("--seed", type=int, action="append")
    args = ap.parse_args()

    m = build_machine(repeater_delay=args.delay)
    assert not m.world.lint(), m.world.lint()[:3]
    for seed in (args.seed or [11]):
        sweep(m, seed, args)


def sweep(m, seed, args):
    cases = hostile_pairs(args.vectors, m.width, seed=seed)
    print(f"delay {args.delay}, seed {seed}, {len(cases)} vectors, "
          f"each moving 10+ levers")
    print("  gt between lever flips | correct | torches burned | worst settle")
    for spacing in [int(s) for s in args.spacings.split(",")]:
        # a fresh engine per spacing: a torch that burned at spacing 0 would
        # otherwise still be dead when spacing 1 is measured
        e = settled_engine(m.world, Engine, use_cache=True, quiet=True)
        good, worst = 0, 0
        for op, a, b in cases:
            m.set_operands(e, a, b, spacing=spacing)
            t = e.now
            m.press_op(e, op)
            e.run_until_stable(400000)
            worst = max(worst, e.now - t)
            want, wflags = m.expect(op, a, b)
            good += (m.read_value(e) == want and m.read_flags(e) == wflags)
        print(f"  {spacing:>21} | {good:>3}/{len(cases)} | "
              f"{len(e.burned_out):>14} | {worst:>5} gt", flush=True)


if __name__ == "__main__":
    main()
