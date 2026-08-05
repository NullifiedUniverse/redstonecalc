"""Break each module on purpose and check the suite notices.

A green suite says the tests pass. It does not say the tests would fail if the
code were wrong, and those are different claims — `tests/test_docs.py` shipped
two guards that could not fail at all until someone tried to defeat them
(DESIGN §22). This asks the same question of the machine itself: change one
thing in a module so it is genuinely wrong, run the fast suite, and require it
to go red.

Each mutation is a *semantic* change, not a typo — a redstone rule off by one, a
gate that ORs where it should invert, a Minecraft convention left unflipped.
They are chosen to be the mistakes that would actually be made, and each one is
reverted whether the run passes, fails or is interrupted.

    python3 tools/mutate_core.py            # every mutation
    python3 tools/mutate_core.py --list
    python3 tools/mutate_core.py --only engine

A mutation that survives is not necessarily a bug. It is a hole: that behaviour
is not pinned by any test, and the next person to change it will find out from
a user rather than from the suite.
"""

import argparse
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: (name, file, what to find, what to put there, what it breaks). Every `find`
#: must be unique in its file: the patch replaces the first occurrence, and a
#: string that also appears in a docstring above the code would mutate the prose
#: and leave the behaviour alone — a mutation that cannot fail is worse than no
#: mutation, because it reports a hole that is not there.
MUTATIONS = [
    ("engine.torch-delay", "rscalc/engine.py",
     "TORCH_DELAY = 2       # game ticks", "TORCH_DELAY = 3       # game ticks",
     "a torch inverting a game tick late"),
    ("engine.burnout-limit", "rscalc/engine.py",
     "BURNOUT_LIMIT = 8", "BURNOUT_LIMIT = 12",
     "a torch surviving four more toggles than the game allows"),
    ("engine.comparator-delay", "rscalc/engine.py",
     "COMPARATOR_DELAY = 2  # game ticks", "COMPARATOR_DELAY = 4  # game ticks",
     "a comparator taking two redstone ticks instead of one"),
    ("engine.dust-decay", "rscalc/engine.py",
     "                if v - 1 > best.get(nxt, 0):\n"
     "                    best[nxt] = v - 1\n"
     "                    heapq.heappush(heap, (-(v - 1), nxt))",
     "                if v > best.get(nxt, 0):\n"
     "                    best[nxt] = v\n"
     "                    heapq.heappush(heap, (-v, nxt))",
     "dust carrying its strength forever instead of losing one per block"),
    ("netlist.gate-polarity", "rscalc/netlist.py",
     "acc = acc or ((not v[src]) if inv else v[src])",
     "acc = acc or (v[src] if inv else (not v[src]))",
     "every gate literal reading the wrong polarity"),
    ("logic.nand-term", "rscalc/logic.py",
     "return nl.gate([(node, want) for node, want in term])",
     "return nl.gate([(node, not want) for node, want in term])",
     "an AND of literals built from the wrong complements"),
    ("alu.subtract-carry-in", "rscalc/alu.py",
     'cin = nl.not_(sub_bar, name="CIN")', 'cin = sub_bar',
     "two's complement subtraction off by one"),
    ("alu.shift-wrap", "rscalc/alu.py",
     "shl_bits = [ZERO_IN if i == 0 else A[i - 1] for i in range(width)]",
     "shl_bits = [A[i - 1] for i in range(width)]",
     "a left shift rotating the top bit round instead of dropping it"),
    ("pla.exit-gap", "rscalc/pla.py",
     "EXIT_GAP = 4         # Z clearance between a gate's last tap and its exit",
     "EXIT_GAP = 1         # Z clearance between a gate's last tap and its exit",
     "a collector exiting on top of its own last tap"),
    ("pla.tap-polarity", "rscalc/pla.py",
     "    if inverting:\n        L._dust((tx, y, z1))",
     "    if not inverting:\n        L._dust((tx, y, z1))",
     "every tap built with the opposite polarity"),
    ("pla.collector-span", "rscalc/pla.py",
     "def plan_collector(cells, taps, max_span=13):",
     "def plan_collector(cells, taps, max_span=15):",
     "a collector run two blocks longer than a tap's signal survives"),
    ("bcd.add-three", "rscalc/bcd.py",
     "return d + 3 if d >= 5 else d", "return d + 3 if d >= 6 else d",
     "the double-dabble threshold one too high"),
    ("display.segment-map", "rscalc/display.py",
     '1: "bc",', '1: "abcdef",',
     "the numeral 1 drawn with every segment of a 0"),
    ("mcbuild.repeater-facing", "rscalc/mcbuild.py",
     '"facing": OPPOSITE[b.facing], "delay": str(b.delay),',
     '"facing": b.facing, "delay": str(b.delay),',
     "Minecraft's output->input repeater convention left unflipped"),
    # this used to mutate the quarter-turn table, and it survived: nothing in
    # the repository ever built a rotated `Placer`, so the whole path was dead.
    # It is gone, and what is left is the frame that every cell really uses.
    ("cells.local-frame", "rscalc/cells.py",
     "        return (o[0] + p[0], o[1] + p[1], o[2] + p[2])",
     "        return (o[0] + p[0], o[1] + p[1], o[2] - p[2])",
     "a cell's local frame mirrored along Z"),
    ("steady.digest-blind", "rscalc/steady.py",
     '        h.update(str(getattr(b, "delay", "")).encode())\n', "",
     "a cached resting state reused after every repeater delay changed"),
    ("steady.lost-lock", "rscalc/steady.py",
     "    b.locked = bool(flags & 8)", "    b.locked = False",
     "a restored world whose repeater locks have all quietly let go"),
    ("machine.digit-places", "rscalc/machine.py",
     "            total += d * (10 ** k)",
     "            total += d * (10 ** (self.ndigits - 1 - k))",
     "the display read most significant digit first"),
    ("harness.tower-climb", "rscalc/harness.py",
     '        w.torch((x, yy + 1, z), attach="down")',
     '        w.torch((x, yy + 1, z), attach="north")',
     "a climbing torch hung off the wrong face"),
    ("panel.row-pitch", "rscalc/panel.py",
     "PANEL_PITCH = 2         # Z spacing between adjacent controls on a row",
     "PANEL_PITCH = 1         # Z spacing between adjacent controls on a row",
     "two control-panel levers sharing a cell"),
    ("keypad.lock-delay", "rscalc/keypad.py",
     "DELAY_REPEATERS = 3   # each is delay-4, so 24 gt: longer than any lock path",
     "DELAY_REPEATERS = 1   # each is delay-4, so 24 gt: longer than any lock path",
     "the lock bus arriving before the key it is meant to hold"),
    ("console.adder-carry", "rscalc/console.py",
     "    P = [nl.or_(a[i], b[i]) for i in range(4)]",
     "    P = [nl.and_(a[i], b[i]) for i in range(4)]",
     "the decimal adder's propagate term built as an AND"),
    ("export.state-fields", "rscalc/export.py",
     "            elif b.kind == \"repeater\":\n                l = b.powered",
     "            elif b.kind == \"repeater\":\n                l = b.locked",
     "the page told a repeater's lock instead of its output"),
]


def load(rel):
    return (ROOT / rel).read_text()


def run_suite(timeout=300):
    """Run the fast suite once. A timeout counts as caught, not as a crash.

    A broken redstone rule can stop a circuit ever settling rather than settling
    wrongly, so some mutations hang instead of failing. The suite takes about
    twenty seconds; anything past five minutes is that, and it is still the
    suite noticing.
    """
    try:
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=ROOT,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "FAIL (never finished)"
    return r.returncode == 0, r.stdout


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="run mutations whose name contains this")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    muts = MUTATIONS
    if args.only:
        muts = [m for m in muts if args.only in m[0]]
    if args.list:
        for name, rel, find, _, breaks in muts:
            n = load(rel).count(find)
            print(f"{name:26} {rel:20} x{n}  {breaks}")
        return 0

    # Every "caught" below means *the suite went red while this was applied*,
    # which is only evidence if the suite was green to begin with. It is not a
    # theoretical worry: run against a working tree where the built pages had
    # drifted from their template, this reported a mutation as caught by
    # `test_pages` — a check that had nothing to do with the module being
    # broken and was failing before the mutation was applied. A run that starts
    # red cannot tell the two apart, so it does not start.
    t0 = time.time()
    ok, _ = run_suite()
    if not ok:
        print(f"the suite is already failing ({time.time()-t0:.0f}s). Every "
              f"mutation would look caught; fix the tree first.")
        return 2
    print(f"suite green in {time.time()-t0:.0f}s — {len(muts)} mutations, "
          f"each one has to turn it red\n")
    survived = []
    for name, rel, find, repl, breaks in muts:
        path = ROOT / rel
        original = path.read_text()
        hits = original.count(find)
        if hits != 1:
            # a `find` that has drifted, or that also matches a docstring: the
            # patch would land somewhere it was not aimed, and a mutation that
            # lands on prose passes for free and reports a hole that is not real
            print(f"  STALE   {name:24} — {find[:40]!r} appears {hits}x "
                  f"in {rel}, needs to appear once")
            survived.append(f"{name} (stale mutation)")
            continue
        t0 = time.time()
        try:
            path.write_text(original.replace(find, repl, 1))
            ok, out = run_suite()
        finally:
            path.write_text(original)          # always, on any path out
        dt = time.time() - t0
        if ok:
            print(f"  SURVIVED {name:23} {dt:4.0f}s — {breaks} "
                  f"is not pinned by any test")
            survived.append(name)
        else:
            # `run_all` ends with "FAILURES ABOVE", which is not a module
            failed = [l.split()[1] for l in out.splitlines()
                      if l.startswith("FAIL ") and ".py" in l.split()[1]]
            print(f"  caught  {name:24} {dt:4.0f}s  by "
                  f"{', '.join(failed[:3]) or 'the suite'}")

    print(f"\n{len(muts)-len(survived)}/{len(muts)} caught")
    if survived:
        print("unpinned behaviour:\n  " + "\n  ".join(survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
