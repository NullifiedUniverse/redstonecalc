"""The numbers in the prose have to be the numbers in the machine.

This project's whole claim is that its figures are measured rather than
asserted, and four times now a documented figure has quietly stopped being true:
the block counts after the gate pitch narrowed, PLAN's BCD table (whose
measurement could not even be run), §13's stability table — twice, the second
time one section after it was corrected — and §13's *explanation* of that table,
which still quoted a repeater count from two builds ago.

Every one of those was found by hand. This finds them instead, and the way it
does so matters, because the obvious way does not work. Checking that the right
number appears *somewhere* in a file passes happily when a file quotes 456,558
four times and one of them is wrong — that check was written, and a deliberate
mutation walked straight through it. So each figure is anchored to the sentence
or table cell that carries it, every capture must equal what the build measures,
and a coverage pass insists that every occurrence of a watched figure sits
inside one of those anchors. Adding a sentence that quotes a headline number
therefore fails until an anchor is added for it — which is the point: an
unanchored occurrence is one that can go stale in silence.

It is deliberately narrow — only load-bearing numbers, the ones a reader would
act on — because a test that checked every integer in a design document would
fail on the page count. The failure message names the file, the line, and what
the number should be, so fixing it is a search-and-replace rather than an
investigation.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..")
README = "README.md"
DESIGN = "docs/DESIGN.md"
PAGE = "docs/preview_template.html"
#: not a document, but the one source file whose comments quote the machine's
#: own dimensions — §20 already had to de-number a constant here for exactly the
#: reason this test exists, so it is checked alongside the prose
MACHINE = "rscalc/machine.py"
#: the planning document. It quotes no current figure, so nothing in it is
#: anchored — but its §10 is a table of a build that has not existed for three
#: sections, which is precisely the failure this file exists to catch.
PLAN = "docs/PLAN.md"
#: files whose figures must equal the current build. PLAN is deliberately not
#: one of them: it is a record of what the machine looked like when each plan
#: was written, so a figure of its own that happens to still be true is a
#: coincidence, not a promise.
FIGURE_FILES = (README, DESIGN, PAGE, MACHINE)
#: everything scanned for figures from builds that no longer exist
FILES = FIGURE_FILES + (PLAN,)


#: set by the mutation self-check below; empty in a normal run
PATCHED = {}


def read(path):
    if path in PATCHED:
        return PATCHED[path]
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def commas(n):
    return f"{n:,}"


_FIGURES = {}


def figures():
    """Every headline number, measured off a freshly built machine."""
    from collections import Counter
    from rscalc.machine import build_machine, SETTLE_GT

    if _FIGURES:                    # the machine takes minutes to build, and
        return _FIGURES             # the mutation self-check needs it repeatedly
    m = build_machine()
    assert not m.world.lint(), m.world.lint()[:3]
    (x0, y0, z0), (x1, y1, z1) = m.world.bounds()
    k = Counter(b.kind for b in m.world.blocks.values())
    f = {
        "blocks": commas(len(m.world.blocks)),
        "x": str(x1 - x0 + 1), "y": str(y1 - y0 + 1), "z": str(z1 - z0 + 1),
        "gates": commas(m.stats["gates"]), "depth": str(m.stats["depth"]),
        "settle": commas(SETTLE_GT),
        "solid": commas(k["solid"]), "dust": commas(k["redstone_wire"]),
        "repeater": commas(k["repeater"]), "torch": commas(k["redstone_torch"]),
        "lamp": commas(k["lamp"]), "glass": commas(k["glass"]),
        "lever": commas(k["lever"]),
    }
    _FIGURES.update(f)
    return f


#: The figures whose *every* occurrence is checked, not just their presence.
#: Short numbers are left out on purpose — 36 and 195 turn up in prose by
#: coincidence, and a guard that fires on coincidence is a guard people switch
#: off. These are long enough to mean only one thing.
WATCHED = ("blocks", "solid", "dust", "repeater", "torch", "settle",
           "gates", "z", "x")

#: (file, pattern, *keys). Every match of the pattern must capture exactly the
#: build's value for those keys, in order. A pattern that matches nothing fails
#: too: the sentence it anchors was rewritten, and the anchor has to follow it.
ANCHORS = [
    # ---- README -------------------------------------------------------
    (README, r"and ([\d,]+) blocks in between", "blocks"),
    (README, r"Merging the ([\d,]+) structure blocks into runs takes it\s+"
             r"from ([\d,]+) draw", "solid", "blocks"),
    # one regex over four rows, because the Mk II summary table further down
    # has the same shape with (correctly) different numbers in it
    (README, r"\| Blocks \| ([\d,]+) \|\n"
             r"\| Extent \| (\d+) x (\d+) x (\d+) \|\n"
             r"\| Gates / depth \| ([\d,]+) / (\d+) \|\n\| Range \|",
             "blocks", "x", "y", "z", "gates", "depth"),
    (README, r"\| Settle \| ([\d,]+) game ticks", "settle"),
    (README, r"\(([\d,]+) either way\)", "torch"),
    (README, r"less — ([\d,]+) repeaters against", "repeater"),
    (README, r"\*\*([\d,]+) blocks become [\d,]+ commands\*\*", "blocks"),
    # ---- DESIGN -------------------------------------------------------
    (DESIGN, r"is ([\d,]+) blocks: an 8-operation ALU", "blocks"),
    (DESIGN, r"the torch count did not move at all, it is ([\d,]+)", "torch"),
    (DESIGN, r"more\*\* repeaters than §15's, ([\d,]+) against", "repeater"),
    (DESIGN, r"The world is ([\d,]+) blocks, and \*\*([\d,]+) of them",
             "blocks", "solid"),
    (DESIGN, r"\| one per block \| ([\d,]+) \|", "blocks"),
    (DESIGN, r"Collapsing ([\d,]+) structure", "solid"),
    (DESIGN, r"longest side — ([\d,]+) blocks", "z"),
    (DESIGN, r"inside a (\d+)×(\d+)×(\d+) volume", "x", "y", "z"),
    (DESIGN, r"\| solid \| ([\d,]+) \|", "solid"),
    (DESIGN, r"\| redstone dust \| ([\d,]+) \|", "dust"),
    (DESIGN, r"\| repeater \| ([\d,]+) \|", "repeater"),
    (DESIGN, r"\| \*\*redstone torch\*\* \| \*\*([\d,]+)\*\* \|", "torch"),
    (DESIGN, r"Of ([\d,]+) torches", "torch"),
    (DESIGN, r"to ([\d,]+)\*\* and 90 game ticks", "z"),
    (DESIGN, r"game ticks sooner\s+\(2,644 → ([\d,]+)\)", "settle"),
    (DESIGN, r"across ([\d,]+) blocks and 8-bit channels", "z"),
    (DESIGN, r"at\s+(\d+) × (\d+) × (\d+) that lands on", "x", "y", "z"),
    (DESIGN, r"dust: ([\d,]+) wires", "dust"),
    (DESIGN, r"a (\d+) × (\d+) × (\d+) slab", "x", "y", "z"),
    (DESIGN, r"A settle is ([\d,]+) game ticks", "settle"),
    # §22 quotes figures while explaining why quoting figures needs a guard,
    # so it is anchored by its own rule
    (DESIGN, r"one occurrence of `([\d,]+)` in README", "blocks"),
    (DESIGN, r"\| 12,424 repeaters \| \*\*([\d,]+)\*\* \|", "repeater"),
    (DESIGN, r"\| 250,840 dust \| \*\*([\d,]+)\*\* \|", "dust"),
    (DESIGN, r"\| 20,644 repeaters \| \*\*([\d,]+)\*\* \|", "repeater"),
    (DESIGN, r"against — ([\d,]+) against 14,464", "repeater"),
    (DESIGN, r"They do now, exactly —\s+([\d,]+) \+ ([\d,]+) \+ ([\d,]+) \+ "
             r"([\d,]+) \+ ([\d,]+) \+ ([\d,]+) \+ ([\d,]+)",
             "solid", "dust", "repeater", "torch", "lamp", "glass", "lever"),
    # ---- the page -----------------------------------------------------
    (PAGE, r"<b>([\d,]+)</b><span>ticks to the answer</span>", "settle"),
    (PAGE, r"did not move — ([\d,]+) either way", "torch"),
    (PAGE, r"not less: ([\d,]+) repeaters against", "repeater"),
    (PAGE, r"([\d,]+) plain structure blocks collapse into stretched\s+"
           r"runs — ([\d,]+)", "solid", "blocks"),
    (PAGE, r"this machine's ([\d,]+) wires", "dust"),
    (PAGE, r"seven kinds of block: ([\d,]+) solid,\s+([\d,]+) dust, "
           r"([\d,]+) repeaters, <b>([\d,]+) torches</b>, ([\d,]+) lamps, "
           r"([\d,]+)\s+glass", "solid", "dust", "repeater", "torch",
           "lamp", "glass"),
    (PAGE, r"of the ([\d,]+) are inverting taps", "torch"),
    (PAGE, r"collapse into fills: ([\d,]+) blocks become", "blocks"),
    (PAGE, r"blocks deep to\s+([\d,]+) and 10% off", "z"),
    (PAGE, r"merging ([\d,]+) structure blocks", "solid"),
    (PAGE, r"a ([\d,]+)-block fog", "z"),
    (PAGE, r"the far end of a ([\d,]+)-block machine", "z"),
    (PAGE, r"Half of this world — ([\d,]+) of ([\d,]+) blocks",
           "solid", "blocks"),
    (PAGE, r"at\s+(\d+) × (\d+) × (\d+) that lands on", "x", "y", "z"),
    (PAGE, r"a machine ([\d,]+) blocks long", "z"),
    (PAGE, r"longest side, which is ([\d,]+) blocks", "z"),
    (PAGE, r"inside a (\d+)×(\d+)×(\d+)", "x", "y", "z"),
    (PAGE, r"is a (\d+) x (\d+) x (\d+) slab", "x", "y", "z"),
    (PAGE, r"A settle is ([\d,]+) game ticks", "settle"),
    # ---- the one source file that quotes the machine at itself ---------
    (MACHINE, r"1,825 blocks deep to ([\d,]+)", "z"),
]


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def test_headline_figures_match_the_machine():
    fig = figures()
    text = {p: read(p) for p in FIGURE_FILES}
    covered = {p: [] for p in text}
    bad = []

    for path, pattern, *keys in ANCHORS:
        hits = list(re.finditer(pattern, text[path]))
        if not hits:
            bad.append(f"{path}: nothing matches {pattern!r} — the sentence it "
                       f"anchors moved, so the anchor has to move with it")
            continue
        for m in hits:
            covered[path].append(m.span())
            for got, key in zip(m.groups(), keys):
                if got != fig[key]:
                    bad.append(f"{path}:{line_of(text[path], m.start())}: "
                               f"{key} reads {got!r}, the build measures "
                               f"{fig[key]!r}")

    # Every occurrence of a watched figure has to sit inside an anchor. A table
    # row is exempt: the before/after tables are history, and their own headers
    # say so (the rows that are *not* history are anchored above by hand).
    for path, body in text.items():
        for key in WATCHED:
            for m in re.finditer(rf"(?<![\d,]){re.escape(fig[key])}(?![\d,])",
                                 body):
                ln = line_of(body, m.start())
                if body.splitlines()[ln - 1].lstrip().startswith("|"):
                    continue
                if any(a <= m.start() and m.end() <= b for a, b in
                       covered[path]):
                    continue
                bad.append(f"{path}:{ln}: {fig[key]} ({key}) is quoted here "
                           f"with no anchor — add one to ANCHORS so it cannot "
                           f"go stale silently")

    assert not bad, "documented figures no longer match the build:\n  " + \
                    "\n  ".join(bad)
    anchors = sum(len(v) for v in covered.values())
    print(f"  headline figures: {anchors} anchored quotations across "
          f"{len(text)} files, every one equal to the build: OK")


#: figures from builds that no longer exist. They are allowed to appear — this
#: document records how the machine got here — but only where a reader can see
#: that is what they are.
SUPERSEDED = {
    "598,230": "the pre-§18 block count",
    "1,825": "the pre-§18 depth",
    "464,366": "the pre-§19 block count",
    "12,424": "the pre-§20 repeater count",
    "14,464": "the pre-§18 repeater count",
    "20,644": "§17's stale repeater count",
    "250,840": "§17's stale dust count",
    "2,644": "the pre-§18 settle",
    "2,374": "the pre-§19 settle",
    "146 torches": "the pre-§18 delay-2 burnout",
    "590,555": "the block count in PLAN §10's snapshot",
    "2,584": "the settle in PLAN §10's snapshot",
}

#: What counts as saying so, in prose: a word that puts the number in the past.
MARKS = ("before", "→", "->", "was ", "were ", "took", "used to", "old ",
         "earlier", "since", "§15", "§18", "§19", "§20", "at the time",
         "against", "the build of §15", "no longer")


def marked(scope, needle):
    """Does this sentence present `needle` as history?

    Either a word from `MARKS`, or the number named as the *start* of a
    transition — "from 1,825 blocks deep to 973" is as clear as "was", and it
    is how a comment naturally describes what a change did.
    """
    if any(k in scope for k in MARKS):
        return True
    return re.search(rf"from\s+\**{re.escape(needle)}", scope) is not None


def sentence_around(lines, i, needle):
    """The sentence carrying `needle`, not the paragraph around it.

    A window of whole lines is too generous, and that is not hypothetical: a
    mutation that turned "the machine *was* 1,825 deep" into "the machine *is*
    1,825 deep" survived it, because the *next* sentence happened to contain
    the word "was". So the neighbouring lines are joined — prose wraps
    mid-claim, and "took the machine from 1,825 blocks deep / to 973" is one
    sentence on two lines — but then split on terminators, and only the
    sentence containing this line's occurrence is returned. Joining widens what
    can be read; splitting narrows what counts. Both are needed.
    """
    prev = lines[i - 1] if i else ""
    nxt = lines[i + 1] if i + 1 < len(lines) else ""
    joined = " ".join(p for p in (prev, lines[i], nxt) if p)
    at = joined.find(needle, len(prev) + 1 if prev else 0)
    if at < 0:                                    # only on the neighbours
        return ""
    edges = ([0] + [m.end() for m in re.finditer(r"(?<=[.;:!?])\s+", joined)]
             + [len(joined)])
    for a, b in zip(edges, edges[1:]):
        if a <= at < b:
            return joined[a:b]
    return joined


def test_no_document_quotes_a_superseded_machine():
    """A figure from a build that no longer exists must be marked as history.

    Two things count as marking it. In prose, a word putting the number in the
    past, looked for in the sentence that carries it and no wider. In a table,
    the row itself: every before/after table here carries its own header, and a
    row of bare numbers under "before | after" is not a claim about the present.

    Without the table rule this fires on half a dozen perfectly honest lines,
    which is worth saying, because a check that cries wolf is a check people
    learn to ignore.
    """
    bad = []
    for path in FILES:
        lines = read(path).splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("|"):
                continue                     # a table row carries its header
            for needle, why in SUPERSEDED.items():
                if needle not in line:
                    continue
                scope = sentence_around(lines, i, needle)
                if not marked(scope, needle):
                    bad.append(f"{path}:{i+1}: {needle} ({why}) with nothing "
                               f"in its sentence marking it as superseded — "
                               f"{line.strip()[:60]}")
    assert not bad, "stale figures presented as current:\n  " + \
                    "\n  ".join(bad)
    print(f"  {len(SUPERSEDED)} superseded figures: each either absent or "
          f"marked as history: OK")


#: Each entry breaks the documentation in a way that has actually happened, or
#: that an earlier version of this file was proven to miss. `--mutate` applies
#: them one at a time and requires the named test to fail. A guard nobody has
#: tried to defeat is a guard nobody knows the strength of.
MUTATIONS = [
    ("one occurrence of a headline figure goes wrong", README,
     "and 456,558 blocks in between", "and 456,528 blocks in between",
     "test_headline_figures_match_the_machine"),
    ("a stale count survives in a table", DESIGN,
     "| repeater | 15,585 |", "| repeater | 20,644 |",
     "test_headline_figures_match_the_machine"),
    ("a new sentence quotes a figure with no anchor", README,
     "digits, and 456,558 blocks in between.",
     "digits, and 456,558 blocks in between. All 456,558 of them.",
     "test_headline_figures_match_the_machine"),
    # the marker has to be in the sentence making the claim. Both of these
    # leave one in an adjacent sentence, which is how the first version of this
    # check was fooled — note that the number wraps onto its own line, so the
    # tense that governs it sits on the line before.
    ("a superseded figure borrows a marker from the sentence after it", DESIGN,
     "The machine was\n**1,825 deep** before §18 fixed this.",
     "The machine is\n**1,825 deep** today.",
     "test_no_document_quotes_a_superseded_machine"),
    ("a superseded figure borrows a marker from the sentence before it", DESIGN,
     "The machine was\n**1,825 deep** before §18 fixed this.",
     "That was the shape of it.\n**1,825 deep** is what this build measures.",
     "test_no_document_quotes_a_superseded_machine"),
]


def mutation_check(verbose=False):
    """Apply each mutation and report which of them the tests above let through.

    The tests are called directly rather than in a subprocess so the machine is
    built once for the whole file; `PATCHED` swaps the document text underneath
    `read` and is always put back.
    """
    figures()                                   # build once, reuse throughout
    missed = []
    for label, path, old, new, name in MUTATIONS:
        body = read(path)
        assert old in body, (f"{path} no longer contains {old!r} — the "
                             f"mutation has to be updated with the prose")
        PATCHED[path] = body.replace(old, new, 1)
        try:
            globals()[name]()
        except AssertionError:
            if verbose:
                print(f"  CAUGHT  {label}")
        else:
            missed.append(f"{label} — {name} passed on a document it should "
                          f"have rejected")
            if verbose:
                print(f"  MISSED  {label}  ({name} passed anyway)")
        finally:
            PATCHED.clear()
    return missed


def test_the_guards_catch_a_broken_document():
    """The checks above are only worth their runtime if they can fail.

    Both of them once could not. The first tested that a figure appeared
    *somewhere* in a file, so corrupting one of four quotations of it passed;
    the second read a window of whole lines, so a stale figure could borrow the
    word "was" from the sentence next door. Neither was caught by review — they
    were caught by breaking the documents on purpose and noticing that nothing
    complained. That experiment is this test.
    """
    missed = mutation_check()
    assert not missed, "the documentation checks let a broken document " \
                       "through:\n  " + "\n  ".join(missed)
    print(f"  {len(MUTATIONS)} deliberately broken documents: every one "
          f"rejected: OK")


if __name__ == "__main__":
    if "--mutate" in sys.argv[1:]:
        print(f"Mutating the documentation {len(MUTATIONS)} ways; "
              f"each one has to be caught\n")
        n = len(mutation_check(verbose=True))
        print(f"\n{len(MUTATIONS)-n}/{len(MUTATIONS)} caught")
        sys.exit(1 if n else 0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} documentation tests\n")
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
