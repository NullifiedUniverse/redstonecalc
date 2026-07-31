"""Inject the circuit bundles into the demo and preview pages."""
import os
ROOT = os.path.join(os.path.dirname(__file__), "..")

demo_src = open(os.path.join(ROOT, "docs/demo_template.html")).read()
open(os.path.join(ROOT, "docs/demo.html"), "w").write(
    demo_src.replace("__CIRCUIT_DATA__", open(os.path.join(ROOT, "out/circuits.json")).read()))

# the preview reuses the demo's engine verbatim so the two can never drift
start = demo_src.index('const KINDS=["solid"')
end = demo_src.rindex("/*", start, demo_src.index("--- renderer */", start))
engine = demo_src[start:end].rstrip()
prev = open(os.path.join(ROOT, "docs/preview_template.html")).read()
open(os.path.join(ROOT, "docs/preview.html"), "w").write(
    prev.replace("__ENGINE__", engine)
        .replace("__CIRCUIT_DATA__", open(os.path.join(ROOT, "out/preview.json")).read()))
for f in ("docs/demo.html", "docs/preview.html"):
    print(f"{f}: {os.path.getsize(os.path.join(ROOT, f))/1024:.0f} KB")
