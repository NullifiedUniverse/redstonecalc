"""Drive the Mk III machine and report what it actually does.

Everything here goes through the player's controls — levers for the operand
bits, a button for the operation — and reads the answer off the lamps. Nothing
reaches inside the machine.

    python3 tools/verify_machine.py --delay 4 --vectors 40
    python3 tools/verify_machine.py --delay 2 --delay 3 --delay 4 --vectors 8
"""

import argparse
import random
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import Engine
from rscalc.machine import build_machine, DEFAULT_DELAY, FLAGS
from rscalc.alu import OPS
from rscalc.steady import settled_engine


def settle_all(engine, budget=400000):
    return engine.run_until_stable(budget)


def apply_operands(m, e, a, b, one_at_a_time):
    """Flip the operand levers. A player moves one at a time; doing them all in
    one tick is the harsher test, because every hazard in the machine fires at
    once."""
    if not one_at_a_time:
        m.set_operands(e, a, b)
        return
    for i in range(m.width):
        for pad, v in (("A", a), ("B", b)):
            want = bool((v >> i) & 1)
            pos = m.levers[f"{pad}{i}"]
            if e.w.blocks[pos].on != want:
                e.set_lever(pos, want)
                settle_all(e)


def run(delay, vectors, seed, one_at_a_time, ops, verbose,
        use_cache=True, verify_state=False):
    t0 = time.time()
    m = build_machine(repeater_delay=delay)
    problems = m.world.lint()
    build_s = time.time() - t0
    t0 = time.time()
    e = settled_engine(m.world, Engine, verify=verify_state,
                       use_cache=use_cache)
    init_s = time.time() - t0

    rng = random.Random(seed)
    cases = []
    # the corners first: they are where carries and flags actually happen
    edge = [(0, 0, 0), (0, 1023, 1), (0, 1023, 1023), (0, 512, 512),
            (1, 0, 1), (1, 1023, 1023), (1, 5, 5), (1, 1000, 1)]
    for op, a, b in edge:
        if op in ops:
            cases.append((op, a, b))
    while len(cases) < vectors:
        cases.append((rng.choice(ops), rng.randrange(1024), rng.randrange(1024)))
    cases = cases[:max(vectors, len(cases))]

    bad, worst, t0 = [], 0, time.time()
    for n, (op, a, b) in enumerate(cases):
        t = e.now
        apply_operands(m, e, a, b, one_at_a_time)
        m.press_op(e, op)
        settle_all(e)
        worst = max(worst, e.now - t)
        got, flags = m.read_value(e), m.read_flags(e)
        want, wflags = m.expect(op, a, b)
        ok = got == want and flags == wflags
        if not ok:
            bad.append((op, a, b, got, want, flags, wflags))
        if verbose:
            print(f"  {OPS[op]:4} {a:4} {b:4} -> {str(got):>5} want {want:<5}"
                  f" {'OK' if ok else 'WRONG'}"
                  f"  flags {''.join(str(flags[f]) for f in FLAGS)}"
                  f"/{''.join(str(wflags[f]) for f in FLAGS)}"
                  f"  burned {len(e.burned_out)}", flush=True)
    run_s = time.time() - t0

    print(f"delay {delay}: {len(cases)-len(bad)}/{len(cases)} correct, "
          f"{len(e.burned_out)} torches burned out, worst settle {worst} gt "
          f"({worst/20:.1f} s in game), lint {len(problems)}")
    print(f"  {m.stats['blocks']} blocks, {m.stats['gates']} gates, "
          f"depth {m.stats['depth']}; build {build_s:.0f}s init {init_s:.0f}s "
          f"run {run_s:.0f}s")
    for op, a, b, got, want, flags, wflags in bad[:5]:
        print(f"    {OPS[op]} {a},{b}: got {got} want {want}; "
              f"flags {flags} want {wflags}")
    return not bad and not e.burned_out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=int, action="append")
    ap.add_argument("--vectors", type=int, default=16)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ops", default="all")
    ap.add_argument("--one-at-a-time", action="store_true",
                    help="move one lever per settle, the way a player does")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-cache", action="store_true",
                    help="recompute the steady state instead of loading it")
    ap.add_argument("--verify-state", action="store_true",
                    help="check the restored state really is a fixed point")
    args = ap.parse_args()
    ops = (list(range(len(OPS))) if args.ops == "all"
           else [OPS.index(o) for o in args.ops.split(",")])
    allok = True
    for d in (args.delay or [DEFAULT_DELAY]):
        allok &= run(d, args.vectors, args.seed, args.one_at_a_time, ops,
                     not args.quiet, use_cache=not args.no_cache,
                     verify_state=args.verify_state)
    sys.exit(0 if allok else 1)
