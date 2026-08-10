"""The pages that ship have to be the pages the templates describe.

`docs/preview.html` and `docs/demo.html` are committed built, because they are
what a reader opens. Everything about them is generated: the template supplies
the page, `tools/build_pages.py` injects the circuit bundle, and the preview
borrows the demo's redstone engine verbatim so there is only ever one copy of
the rules in the repository.

That is three inputs per page, none of which announce themselves when they
change. Editing a template and forgetting to rebuild leaves a shipped page that
disagrees with its own source — and the browser checks run against the built
page, so they would go on passing while testing something nobody can reproduce.

This renders both pages in memory and compares. It writes nothing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.build_pages import ROOT, render


def test_built_pages_match_their_sources():
    stale = []
    for path, want in render().items():
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            stale.append(f"{path} has never been built")
            continue
        with open(full, encoding="utf-8") as f:
            got = f.read()
        if got == want:
            continue
        at = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b),
                  min(len(got), len(want)))
        stale.append(f"{path} differs from its template at byte {at:,} "
                     f"({len(got):,} bytes on disk, {len(want):,} rendered): "
                     f"{got[at:at+60]!r} vs {want[at:at+60]!r}")
    assert not stale, ("run `python3 tools/build_pages.py`:\n  "
                       + "\n  ".join(stale))
    print(f"  {len(render())} built pages byte-identical to their templates "
          f"and bundles: OK")


def test_nothing_the_page_needs_is_fetched_from_anywhere():
    """One file, no requests. The artifact runtime enforces it; this explains it.

    The published page is served under a policy that blocks every external host,
    and a blocked `<script src>` does not fail loudly — the page arrives without
    whatever it was, and without an error anyone will see. So the animation
    library is inlined from `vendor/`, and the page may not contain a reference
    to a CDN even as a fallback, because a fallback that cannot load is just a
    slower way to be wrong.
    """
    import re
    from tools.build_pages import render
    page = render()["docs/preview.html"]
    # what fetches, not what is merely mentioned: a vendored library's own
    # banner comment names its home page, and that costs nobody a request
    fetches = [
        (r"""<(?:script|img|link|iframe|source|video|audio)\b[^>]*?"""
         r"""\b(?:src|href)\s*=\s*["']?(?:https?:)?//""", "a tag pointing off-site"),
        (r"@import\s+(?:url\()?['\"]?(?:https?:)?//", "a CSS @import"),
        (r"url\(\s*['\"]?(?:https?:)?//", "a CSS url()"),
        (r"\bfetch\s*\(\s*['\"`](?:https?:)?//", "a fetch()"),
        (r"\bnew\s+(?:Image|Audio)\b[\s\S]{0,80}?\.src\s*=\s*['\"`](?:https?:)?//",
         "an image or audio load"),
    ]
    bad = [why for pat, why in fetches if re.search(pat, page, re.I)]
    assert not bad, f"the page reaches outside itself: {bad}"
    assert "GSAP 3." in page, "the animation library is not in the built page"
    assert "ScrollTrigger" in page, "ScrollTrigger is not in the built page"
    assert "fflate" in page, "the zip library is not in the built page"
    print(f"  the built page is {len(page)//1024} KB and fetches nothing: OK")


def test_neither_page_has_an_engine_of_its_own():
    """One copy of the redstone rules, in a file of its own.

    Both templates carry an `__ENGINE__` placeholder; `docs/engine.js` holds the
    only implementation. A second copy of dust decay and torch burnout is a
    second chance to be wrong, and the browser checks would happily verify
    whichever one their page happened to get.

    This used to check only the preview, because the engine lived *inside* the
    demo page and the preview was built by slicing it back out. That made the
    older Mk I demo a source file for the newer Mk III one — so a change to the
    demo could alter what the published page runs on, and nothing said so.
    """
    pages = ["docs/preview_template.html", "docs/demo_template.html"]
    for rel in pages:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            tpl = f.read()
        assert tpl.count("__ENGINE__") == 1, \
            f"{rel} must take the engine, exactly once"
        assert 'const KINDS=["solid"' not in tpl, \
            f"{rel} has grown a redstone engine of its own"
    with open(os.path.join(ROOT, "docs/engine.js"), encoding="utf-8") as f:
        eng = f.read()
    assert 'const KINDS=["solid"' in eng and "class Engine" in eng, \
        "docs/engine.js is not the engine any more"
    print(f"  {len(pages)} pages, 0 engines between them, and one "
          f"docs/engine.js: OK")


def test_nothing_at_the_top_level_touches_the_machine():
    """`circ`, `world` and `eng` do not exist until the boot function runs.

    They are declared empty and filled by an `async` IIFE at the end of the
    page: unpack the blob, build the world, wire the engine. Every statement at
    the top level of the script runs *before* that finishes, so one that reads
    `circ.mc` throws a TypeError — and the boot never reaches `__ready`, the
    page shows its loading line forever, and nothing in the console says why
    unless somebody is watching it.

    That is not hypothetical. Reading the datapack's tick count off the bundle
    was written as a top-level line beside the button's `onclick`, and it took
    two 300-second browser timeouts to find. This is the same check, statically,
    in the time it takes to read the file.
    """
    import re
    bad = []
    for rel in ["docs/preview_template.html", "docs/demo_template.html"]:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            lines = f.read().split("\n")
        depth = 0
        for n, line in enumerate(lines, 1):
            s = line.rstrip()
            # a statement is top-level if no brace is open above it; comments,
            # declarations and function bodies are all fine
            # only what is evaluated *now*: anything past the line's first `=>`
            # or `function` is a body that runs later, and `bStep.onclick =
            # () => eng.tick()` is exactly right
            now = s.split("=>")[0].split("function")[0]
            if (depth == 0 and re.match(r"^[^\s/*}]", s)
                    and re.search(r"\b(circ|world|eng)\s*\.", now)
                    and not s.startswith(("function", "const ", "let ", "var ",
                                          "class ", "//", "/*", "*"))):
                bad.append(f"{rel}:{n}: {s[:70]}")
            depth += s.count("{") - s.count("}")
    assert not bad, ("these run before the machine exists, so the page would "
                     "never finish booting:\n  " + "\n  ".join(bad))
    print("  no top-level statement reads the machine before boot fills it: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} page build tests\n")
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
