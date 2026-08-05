"""Run every test module and summarise.

    python3 tests/run_all.py           # everything except the slowest
    python3 tests/run_all.py --slow    # including the Mk III machine

The machine tests are held back by default because standing up half a million
blocks takes a few minutes the first time; after that the resting state is
cached and they run in seconds.
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAST = [
    ("test_mechanics.py", "redstone rules"),
    ("test_minecraft_rules.py", "Minecraft rules, and engine vs engine"),
    ("test_cells.py", "gate cells on placed blocks"),
    ("test_logic.py", "netlist helpers, against truth tables"),
    ("test_alu_logic.py", "the ALU's logic, exhaustive at 4 bits"),
    ("test_console_logic.py", "the Mk II console's logic, all 100 key pairs"),
    ("test_pla.py", "place and route"),
    ("test_bcd.py", "binary to BCD"),
    ("test_display.py", "seven-segment display and the torch-free latch"),
    ("test_errors.py", "error handling and structural invariants"),
    ("test_steady.py", "the steady-state cache every measurement rests on"),
    ("test_mcbuild.py", "Minecraft build output"),
    ("test_export.py", "the blob the page loads, decoded as the page decodes it"),
    ("test_docs.py", "the prose against the machine it describes"),
    ("test_pages.py", "the shipped pages against their templates"),
]
SLOW = [
    ("test_exhaustive.py", "10-bit logic, every operand pair"),
    ("test_console.py", "Mk II decimal console"),
    ("test_alu.py", "Mk I ALU, exhaustive at 4 bits"),
    ("test_machine.py", "Mk III 10-bit machine"),
]


def run(mod, label):
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(HERE, mod)],
                       capture_output=True, text=True, cwd=ROOT)
    out = p.stdout.strip().splitlines()
    tail = out[-1] if out else "(no output)"
    mark = "ok  " if p.returncode == 0 else "FAIL"
    print(f"{mark} {mod:22} {label:44} {tail:14} {time.time()-t0:5.0f}s")
    if p.returncode != 0:
        for line in out[-25:]:
            print(f"       {line}")
        if p.stderr.strip():
            print(f"       stderr: {p.stderr.strip().splitlines()[-1]}")
    return p.returncode == 0


if __name__ == "__main__":
    mods = list(FAST)
    if "--slow" in sys.argv or "--all" in sys.argv:
        mods += SLOW
    print(f"{'':5}{'module':22} {'covers':44} {'result':14} {'time':>5}")
    print("-" * 94)
    t0 = time.time()
    ok = all([run(m, l) for m, l in mods])
    print("-" * 94)
    print(f"{'ALL PASSED' if ok else 'FAILURES ABOVE'} — "
          f"{len(mods)} modules in {time.time()-t0:.0f}s")
    if "--slow" not in sys.argv and "--all" not in sys.argv:
        print(f"({len(SLOW)} slower modules held back; run with --slow)")
    sys.exit(0 if ok else 1)
