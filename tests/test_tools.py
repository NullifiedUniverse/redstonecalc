"""The tools, which nothing tested and which four bugs of one class went into.

`tools/` is where the measurements come from — the verifiers, the exporters,
the experiments the design document quotes. Nothing in this suite ever touched
any of it, and the cost of that was four separate instances of exactly one
mistake: a repeater delay defaulting to a setting the machine does not ship at.

  * `debug_machine.py` defaulted to **2**, which §13 measures as 1 vector in 16
    correct and 140 torches burned. Run with no arguments it reported "PLA
    disagrees with logic on 9 outputs" for a machine that was perfectly well.
  * `verify_machine.py` had `4` written into the loop rather than the constant,
    so it would have gone on saying 4 after the machine moved.
  * `experiment_hostile_inputs.py` defaulted to 3.
  * `build_preview.py` defaulted to 3 — and that one writes `out/preview.json`,
    which is embedded in the **published page**. The README's example passed 4
    explicitly, so what shipped was right; running the tool the obvious way
    would have replaced it with a machine that gets the answers wrong.

Every one of those was found by a person reading the file. This is the check
that finds the fifth: any tool that takes a repeater delay defaults to the
verified one, every tool answers `--help` without doing any work, and every tool
says what it is for.

It deliberately does *not* run the tools. Several build half a million blocks.
What it can do cheaply is parse them, and ask the argument parser itself.
"""

import ast
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.machine import DEFAULT_DELAY

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = sorted(p for p in (ROOT / "tools").glob("*.py")
               if p.name != "__init__.py")

#: tools with no argument parser, because they take no arguments. Listed rather
#: than inferred: a tool that *loses* its parser should fail this, not be
#: silently excused by it.
NO_ARGUMENTS = {"build_pages.py", "profile_path.py",
                "experiment_comparator_tap.py"}


def test_every_tool_says_what_it_is_for():
    """A directory of fourteen scripts is navigable only if each one opens by
    saying what it does. Parsed, not imported — importing costs a machine."""
    bare = []
    for p in TOOLS:
        doc = ast.get_docstring(ast.parse(p.read_text()))
        if not doc or len(doc.strip()) < 30:
            bare.append(p.name)
    assert not bare, f"tools with no useful docstring: {bare}"
    print(f"  all {len(TOOLS)} tools open with a docstring: OK")


def test_no_tool_reads_argv_by_hand():
    """`sys.argv[1]` is how `build_preview` came to default the published
    page's repeater delay to a setting that burns torches: there was no parser
    to put the default in, no `--help` to show it, and no place to warn."""
    manual = []
    for p in TOOLS:
        src = p.read_text()
        tree = ast.parse(src)
        uses_argv = any(
            isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Attribute)
            and n.value.attr == "argv"
            for n in ast.walk(tree))
        # `"--flag" in sys.argv` is the same fault wearing a different hat
        scans_argv = any(
            isinstance(n, ast.Compare)
            and any(isinstance(o, ast.In) for o in n.ops)
            and "sys.argv" in ast.unparse(n)
            for n in ast.walk(tree))
        if (uses_argv or scans_argv) and p.name not in NO_ARGUMENTS:
            manual.append(p.name)
    assert not manual, (f"tools parsing sys.argv by hand: {manual} — use "
                        f"argparse, so the default is visible in --help")
    print(f"  no tool picks its arguments out of sys.argv by hand: OK")


def touches_the_machine(src):
    """Does this file build or drive the Mk III?

    The distinction matters, and the first version of this check got it wrong.
    `DEFAULT_DELAY` is the *Mk III's* setting; `compile_netlist` defaults to 1
    because the Mk II circuits are small and stable there, and a `Block`'s own
    `delay=1` is a repeater's 1-to-4 setting, a different quantity that happens
    to share a name. Demanding 4 everywhere fired on six perfectly correct
    lines — and a check that cries wolf is one people learn to ignore.
    """
    return "build_machine" in src or "from rscalc.machine import" in src


def test_every_mk_iii_delay_default_is_the_verified_one():
    """The mistake that has actually been made, four times.

    In a tool that stands up the Mk III, a `--delay` default of anything but
    `DEFAULT_DELAY` is a tool that lies about a healthy machine — or, in
    `build_preview`'s case, publishes a broken one. `None` is fine: it means
    "ask the module", which is the same answer by a different route.
    """
    wrong = []
    for p in TOOLS:
        src = p.read_text()
        if not touches_the_machine(src):
            continue
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"
                    and any(isinstance(a, ast.Constant)
                            and a.value in ("--delay", "--repeater-delay")
                            for a in node.args)):
                for kw in node.keywords:
                    if kw.arg != "default":
                        continue
                    ok = (isinstance(kw.value, ast.Name)
                          and kw.value.id == "DEFAULT_DELAY") or \
                         (isinstance(kw.value, ast.Constant)
                          and kw.value.value is None)
                    if not ok:
                        wrong.append(f"{p.name}: --delay defaults to "
                                     f"{ast.unparse(kw.value)}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                names = [x.arg for x in a.args[-len(a.defaults):]] \
                    if a.defaults else []
                for name, dflt in zip(names, a.defaults):
                    if name not in ("delay", "repeater_delay"):
                        continue
                    if isinstance(dflt, ast.Constant) and dflt.value is not None:
                        wrong.append(f"{p.name}: {node.name}({name}="
                                     f"{dflt.value!r}) is a literal")
    assert not wrong, ("a Mk III tool defaults its delay to a number rather "
                       f"than to DEFAULT_DELAY:\n  " + "\n  ".join(wrong))
    n = sum(1 for p in TOOLS if touches_the_machine(p.read_text()))
    print(f"  all {n} tools that stand up the Mk III default to "
          f"DEFAULT_DELAY ({DEFAULT_DELAY}): OK")


def test_help_costs_nothing_and_works():
    """`--help` has to answer before any work happens.

    A parser built *after* the machine is compiled is a `--help` that takes four
    minutes, which is the same as not having one. Each tool that takes arguments
    gets three seconds.
    """
    slow, broken = [], []
    for p in TOOLS:
        if p.name in NO_ARGUMENTS:
            continue
        try:
            r = subprocess.run([sys.executable, str(p), "--help"], cwd=ROOT,
                               capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            slow.append(p.name)
            continue
        if r.returncode != 0 or "usage:" not in r.stdout.lower():
            broken.append(f"{p.name} (exit {r.returncode})")
    assert not slow, (f"--help does real work before printing in: {slow}")
    assert not broken, f"--help fails in: {broken}"
    n = len(TOOLS) - len(NO_ARGUMENTS)
    print(f"  {n} tools answer --help in under 15s, and mean it: OK")


def test_the_published_bundle_was_built_at_the_verified_delay():
    """`out/preview.json` is embedded in `docs/preview.html` and is the machine
    a reader actually operates. Whatever delay it was built at is the delay the
    published page runs at, so it is worth one line to check."""
    import json
    p = ROOT / "out/preview.json"
    if not p.exists():
        print("  (no bundle built yet — skipped)")
        return
    circ = json.loads(p.read_text())["circuits"][0]
    assert circ.get("delay") == DEFAULT_DELAY, (
        f"the shipped bundle was built at repeater delay {circ.get('delay')}, "
        f"not the verified {DEFAULT_DELAY} — the page is running a machine "
        f"§13 measured as burning torches")
    print(f"  the shipped bundle is the machine at delay {DEFAULT_DELAY}, "
          f"{circ['n']:,} blocks: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} tool tests\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as ex:
            print(f"  FAIL {t.__name__}: {ex}")
            failed += 1
        except Exception:
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
