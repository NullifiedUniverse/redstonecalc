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


def test_the_preview_has_no_engine_of_its_own():
    """One copy of the redstone rules, not two.

    The preview template carries a `__ENGINE__` placeholder rather than an
    engine. If someone ever pastes one in, this fails — a second implementation
    of dust decay and torch burnout is a second chance to be wrong, and the
    browser check would happily verify the wrong one.
    """
    with open(os.path.join(ROOT, "docs/preview_template.html"),
              encoding="utf-8") as f:
        tpl = f.read()
    assert tpl.count("__ENGINE__") == 1, \
        "the preview template must take the demo's engine, exactly once"
    assert 'const KINDS=["solid"' not in tpl, \
        "the preview template has grown a redstone engine of its own"
    print("  the preview still borrows the demo's engine rather than "
          "carrying one: OK")


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
