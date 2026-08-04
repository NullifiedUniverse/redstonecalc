"""Inject the circuit bundles into the demo and preview pages.

The two pages are committed built, because they are the artefact — a reader
opens `docs/preview.html`, not a template and a JSON file. That means the built
copy can drift from the template it was made of, silently, the moment someone
edits one and forgets to run this. `tests/test_pages.py` renders both here and
compares, so the drift lasts exactly until the next test run.
"""

import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def engine_source(demo_src):
    """The demo's redstone engine, lifted verbatim so the two cannot drift.

    The preview page has no engine of its own: it is handed this one at build
    time. Two copies of the redstone rules in one repository would be two
    chances to be wrong differently.
    """
    start = demo_src.index('const KINDS=["solid"')
    end = demo_src.rindex("/*", start, demo_src.index("--- renderer */", start))
    return demo_src[start:end].rstrip()


def render():
    """Both pages as strings, without writing anything."""
    demo_src = read("docs/demo_template.html")
    out = {"docs/demo.html": demo_src.replace("__CIRCUIT_DATA__",
                                              read("out/circuits.json"))}
    out["docs/preview.html"] = (
        read("docs/preview_template.html")
        .replace("__ENGINE__", engine_source(demo_src))
        .replace("__CIRCUIT_DATA__", read("out/preview.json")))
    return out


def main():
    for path, body in render().items():
        with open(os.path.join(ROOT, path), "w", encoding="utf-8") as f:
            f.write(body)
        print(f"{path}: {len(body.encode())/1024:.0f} KB")


if __name__ == "__main__":
    main()
