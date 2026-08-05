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


def engine_source():
    """The redstone engine, from the one file that holds it.

    Neither page has an engine of its own; both are handed this at build time,
    because two copies of the redstone rules in one repository would be two
    chances to be wrong differently.

    It used to live *inside* `docs/demo_template.html`, and this function cut it
    back out with `index()` and `rindex()` over two marker strings. That made the
    older Mk I demo page a source file for the newer Mk III one: editing the demo
    risked silently changing what the published page runs on, the slice depended
    on a comment above it staying put, and the demo page carried a note asking
    people not to move its first line. One file, read whole, ends all of that.
    """
    return read("docs/engine.js").rstrip()


def motion_source():
    """GSAP and ScrollTrigger, inlined.

    Not a `<script src>` to a CDN: the preview is published as an artifact, and
    the artifact runtime serves it under a policy that blocks every external
    host. A blocked CDN tag does not fail loudly — the page simply arrives with
    no animation and no error anyone will see. Inlining keeps the page a single
    self-contained file, which is what it has always been.
    """
    return "\n".join(read(f"vendor/gsap/{f}")
                     for f in ("gsap.min.js", "ScrollTrigger.min.js"))


def render():
    """Both pages as strings, without writing anything."""
    engine = engine_source()
    out = {"docs/demo.html": (
        read("docs/demo_template.html")
        .replace("__ENGINE__", engine)
        .replace("__CIRCUIT_DATA__", read("out/circuits.json")))}
    out["docs/preview.html"] = (
        read("docs/preview_template.html")
        .replace("__GSAP__", motion_source())
        .replace("__ENGINE__", engine)
        .replace("__CIRCUIT_DATA__", read("out/preview.json")))
    return out


def main():
    for path, body in render().items():
        with open(os.path.join(ROOT, path), "w", encoding="utf-8") as f:
            f.write(body)
        print(f"{path}: {len(body.encode())/1024:.0f} KB")


if __name__ == "__main__":
    main()
