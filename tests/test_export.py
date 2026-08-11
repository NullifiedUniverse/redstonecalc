"""The blob the page loads, decoded the way the page decodes it.

`rscalc/export.py` turns a compiled world into the bytes `docs/preview.html`
unpacks at boot. Until now nothing in this suite touched it: the only caller is
`tools/build_preview.py`, and the only thing that ever checked its output was
the browser check, which the mutation sweep cannot reach and which needs a
browser to run at all.

That is a bad place for a hole, because this file is a **contract between two
languages**. The Python side writes a kind byte; the JavaScript side indexes
`KINDS` with it. Insert a block type in the middle of one list and every block
in the machine is drawn as the wrong thing — no error, no exception, a page
that renders a plausible machine made of the wrong materials.

The subtler half is the state translation. The two engines keep the same facts
in different fields: a comparator's output is `out` in Python and `power` in the
browser, a repeater's is `powered` and `lit`, a lever's is `on` and `lit`. Get
one of those wrong and the page boots into a resting state that is *almost*
right, which is worse than one that is obviously broken.

So: encode, decode exactly as the browser does, and compare block for block.
"""

import os
import re
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import Block, World, Engine
from rscalc.export import (DIRS6, KIND_ID, KINDS, encode_world, export_circuit,
                           write_bundle)

ROOT = os.path.join(os.path.dirname(__file__), "..")
#: the engine is one file now, injected into both pages at build time
ENGINE_JS = os.path.join(ROOT, "docs/engine.js")


def build():
    """One of every kind, and every meta field in a state worth checking."""
    w = World()
    for x in range(-2, 12):
        w.solid((x, -1, 0))
    w.lever((-2, 0, 0), attach="down", on=True)
    w.wire((-1, 0, 0))
    w.wire((0, 0, 0))
    w.repeater((1, 0, 0), facing="east", delay=3)
    w.solid((2, 0, 0))
    w.wire((3, 0, 0))
    w.torch((4, 0, 0), attach="down")
    w.comparator((5, 0, 0), facing="east", mode="subtract")
    w.lamp((6, 0, 0))
    w.redstone_block((7, 0, 0))
    w.set((8, 0, 0), Block("glass"))
    w.repeater((9, 0, 0), facing="west", delay=1)
    w.torch((10, 0, 0), attach="west")
    w.lever((11, 0, 0), attach="up", on=False)
    return w


def decode(enc):
    """Exactly what `World`'s constructor in the demo page does.

    Kept deliberately literal — offsets, widths and byte order all written out —
    because a decoder that shares helpers with the encoder proves only that the
    two halves of one function agree with each other.
    """
    import base64
    import gzip
    raw = gzip.decompress(base64.b64decode(enc["data"]))
    n = enc["n"]
    out = []
    for i in range(n):
        x = struct.unpack_from("<H", raw, i * 2)[0]
        y = struct.unpack_from("<H", raw, n * 2 + i * 2)[0]
        z = struct.unpack_from("<H", raw, n * 4 + i * 2)[0]
        out.append({"pos": (x, y, z), "kind": raw[n * 6 + i],
                    "meta": raw[n * 7 + i]})
    state = None
    if "state" in enc:
        s = gzip.decompress(base64.b64decode(enc["state"]))
        state = [{"power": s[i], "lit": s[n + i]} for i in range(n)]
    return out, state


def test_every_block_survives_the_round_trip():
    w = build()
    enc = encode_world(w)
    got, _ = decode(enc)
    x0, y0, z0 = enc["origin"]
    assert len(got) == len(w.blocks) == enc["n"]

    wrong = []
    for g in got:
        pos = (g["pos"][0] + x0, g["pos"][1] + y0, g["pos"][2] + z0)
        b = w.blocks.get(pos)
        if b is None:
            wrong.append(f"{pos} is not in the world at all")
            continue
        if KINDS[g["kind"]] != b.kind:
            wrong.append(f"{pos}: {KINDS[g['kind']]} not {b.kind}")
            continue
        m = g["meta"]
        if b.kind == "repeater":
            if DIRS6[m & 7] != b.facing or (m >> 3) + 1 != b.delay:
                wrong.append(f"{pos}: repeater {DIRS6[m & 7]} delay "
                             f"{(m >> 3) + 1}, wanted {b.facing} {b.delay}")
        elif b.kind == "comparator":
            mode = "subtract" if (m >> 3) & 1 else "compare"
            if DIRS6[m & 7] != b.facing or mode != b.mode:
                wrong.append(f"{pos}: comparator {DIRS6[m & 7]} {mode}")
        elif b.kind == "redstone_torch":
            if DIRS6[m] != b.attach:
                wrong.append(f"{pos}: torch on {DIRS6[m]} not {b.attach}")
        elif b.kind == "lever":
            if DIRS6[m & 7] != b.attach or bool((m >> 3) & 1) != b.on:
                wrong.append(f"{pos}: lever {DIRS6[m & 7]} "
                             f"{'on' if (m >> 3) & 1 else 'off'}")
    assert not wrong, f"{len(wrong)} blocks changed: {wrong[:4]}"

    (a, b_, c), (d, e, f) = w.bounds()
    assert enc["origin"] == [a, b_, c], enc["origin"]
    assert enc["dims"] == [d - a + 1, e - b_ + 1, f - c + 1], enc["dims"]
    print(f"  {len(got)} blocks, {len(set(KINDS[g['kind']] for g in got))} "
          f"kinds, every position and meta field identical: OK")


def test_the_settled_state_is_translated_and_not_copied():
    """The two engines keep the same facts under different names.

    A comparator's output lives in `out` here and in `power` there; a repeater's
    in `powered` and `lit`; a lever's in `on` and `lit`. This is the translation,
    and getting it wrong ships a page that boots into a resting state that is
    almost right.
    """
    w = build()
    e = Engine(w)
    e.initialize_steady()
    enc = encode_world(w, e)
    got, state = decode(enc)
    assert state is not None, "asking for state produced none"
    x0, y0, z0 = enc["origin"]

    wrong, seen = [], set()
    for g, s in zip(got, state):
        pos = (g["pos"][0] + x0, g["pos"][1] + y0, g["pos"][2] + z0)
        b = w.blocks[pos]
        want_p = (b.out if b.kind == "comparator"
                  else b.power if b.kind in ("redstone_wire", "lamp") else 0)
        want_l = (b.lit if b.kind == "redstone_torch"
                  else b.powered if b.kind == "repeater"
                  else b.on if b.kind == "lever" else False)
        if s["power"] != min(255, int(want_p or 0)):
            wrong.append(f"{pos} {b.kind}: power {s['power']} not {want_p}")
        if bool(s["lit"]) != bool(want_l):
            wrong.append(f"{pos} {b.kind}: lit {s['lit']} not {want_l}")
        if s["power"] or s["lit"]:
            seen.add(b.kind)
    assert not wrong, f"{len(wrong)} blocks mistranslated: {wrong[:4]}"
    # and the check is only worth anything if the world had something to say
    for k in ("redstone_wire", "redstone_torch", "repeater", "lever"):
        assert k in seen, (f"no {k} carried any state, so a translation that "
                           f"dropped it would have passed")
    print(f"  settled state translated for {len(seen)} kinds that carry one, "
          f"and nothing invented for the rest: OK")


def test_blocks_come_out_in_the_order_the_renderer_assumes():
    """Sorted by (y, z, x). The page merges neighbouring solids into stretched
    runs as it walks the list, so the order is not presentation — it is what
    makes a quarter of a million blocks draw at all."""
    w = build()
    got, _ = decode(encode_world(w))
    keys = [(g["pos"][1], g["pos"][2], g["pos"][0]) for g in got]
    assert keys == sorted(keys), "the blob is not sorted by (y, z, x)"
    print(f"  {len(keys)} blocks in (y, z, x) order: OK")


def test_python_and_the_browser_agree_on_the_tables():
    """A kind byte means nothing on its own — it is an index into a list that
    lives in the other language. Insert one entry in the middle of either and
    every block in the machine is drawn as the wrong material, silently."""
    with open(ENGINE_JS, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"const KINDS=\[(.*?)\];", src, re.S)
    assert m, "docs/engine.js no longer declares KINDS where this can find it"
    js_kinds = re.findall(r'"([a-z_]+)"', m.group(1))
    assert js_kinds == KINDS, (f"kind tables disagree:\n  py {KINDS}\n  js "
                               f"{js_kinds}")

    # the direction table is written as vectors there, so compare the vectors
    m = re.search(r"const DIRV=\[(.*?)\];", src, re.S)
    assert m, "docs/engine.js no longer declares DIRV"
    js_dirs = [tuple(int(v) for v in t.split(","))
               for t in re.findall(r"\[(-?\d+,-?\d+,-?\d+)\]", m.group(1))]
    from rscalc.engine import DIRS
    py_dirs = [DIRS[d] for d in DIRS6]
    assert js_dirs == py_dirs, (f"direction tables disagree:\n  py {py_dirs}\n"
                                f"  js {js_dirs}")

    names = ["K_SOLID", "K_WIRE", "K_TORCH", "K_REP", "K_CMP", "K_LEVER",
             "K_LAMP", "K_RBLOCK", "K_GLASS"]
    for want, name in enumerate(names):
        m = re.search(rf"\b{name}=(\d+)", src)
        assert m, f"docs/engine.js no longer defines {name}"
        assert int(m.group(1)) == want, \
            f"{name} is {m.group(1)} in the engine, {want} in KINDS"
    print(f"  {len(KINDS)} kinds and {len(DIRS6)} directions identical in "
          f"Python and in the engine, and all {len(names)} constants agree: OK")


def test_a_world_too_big_to_encode_says_so():
    """Coordinates are 16 bits relative to the origin. The machine's longest
    side is under a thousand, so this is headroom rather than a limit — but it
    has to fail loudly rather than wrap a block round to the far side."""
    w = World()
    w.solid((0, 0, 0))
    w.solid((70000, 0, 0))
    try:
        encode_world(w)
    except struct.error:
        print("  a world wider than 16 bits refuses to encode: OK")
        return
    raise AssertionError("a 70,000-block span encoded without complaint, so "
                         "some block wrapped round to the wrong side")


def test_a_circuit_puts_its_landmarks_in_the_same_frame():
    """`levers` and `outputs` are read by the page as indices into the same
    grid as the blocks, so they have to be relative to the same origin. An
    absolute coordinate here points at nothing and reads as a missing lever."""
    class FakeLayout:
        levers = {"A0": (-2, 0, 0)}
        outputs = {"R0": (6, 0, 0)}
        stats = {"gates": 1}

    w = build()
    c = export_circuit("t", "T", w, FakeLayout())
    x0, y0, z0 = c["origin"]
    assert c["levers"]["A0"] == [-2 - x0, -y0, -z0], c["levers"]
    assert c["outputs"]["R0"] == [6 - x0, -y0, -z0], c["outputs"]
    got, _ = decode(c)
    have = {tuple(g["pos"]) for g in got}
    for name, p in list(c["levers"].items()) + list(c["outputs"].items()):
        assert tuple(p) in have, f"{name} at {p} is not a block in the blob"
    assert c["stats"] == {"gates": 1} and c["name"] == "t"
    print("  levers and outputs land on real blocks in the blob's own "
          "coordinates: OK")


def test_the_bundle_is_one_line_of_json():
    import json
    import tempfile
    w = build()
    c = encode_world(w)
    c.update({"name": "t", "title": "T"})
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "b.json")
        size = write_bundle(p, [c])
        with open(p) as f:
            body = f.read()
    assert size == len(body)
    assert "\n" not in body, "the bundle is pretty-printed; it is embedded in " \
                             "a page and pays for every byte"
    assert json.loads(body)["circuits"][0]["name"] == "t"
    print(f"  the bundle is {size:,} bytes of single-line JSON: OK")


def test_the_page_gets_its_minecraft_table_from_the_exporter():
    """The page writes a datapack. It must not know how to.

    Every orientation convention in this project is stated once, in
    `mcbuild.block_state` — a repeater's facing flips on the way out, a wall
    torch points away from its support — and each of them fails *silently* when
    wrong: the build looks perfect and computes nothing. So the bundle carries
    a (kind, meta) -> block state table computed from that function, and the
    page looks answers up rather than deriving them. This checks the shipped
    table against `mcbuild` directly, because a table that has drifted is a
    second opinion about a convention with nobody to referee it.
    """
    import json
    from rscalc import mcbuild
    path = os.path.join(ROOT, "out/preview.json")
    if not os.path.exists(path):
        print("  (no bundle built yet — skipped)")
        return
    circ = json.load(open(path))["circuits"][0]
    mc = circ.get("mc")
    assert mc, "the bundle carries no Minecraft table, so the page cannot " \
               "write a datapack without inventing one"
    assert mc["pack_meta"] == mcbuild.pack_meta(mc["pack_meta"]["pack"]
                                                ["description"])
    # The same argument covers the pack's *shape*, not just its blocks — and it
    # is settled differently now. The page used to write its own copy of the
    # control functions, and force-loading went into the exporter alone: the
    # browser's pack shipped with no `load`, so in a real world the far end of
    # the machine never ticks. There is no second copy any more. The exporter
    # ships the finished text and the page writes those bytes out, so the only
    # thing left to check is that the text is there and covers the entry points
    # the page's own README tells a reader to type.
    files = mc["control_files"]
    ns = mc["namespace"]
    for entry in ("build", "clear", "load", "unload", "status", "help"):
        assert f"{entry}.mcfunction" in files, entry
    assert f"function {ns}:load" in files["build.mcfunction"], \
        "build must force-load before it places anything"
    assert "forceload add" in files["load_tiles.mcfunction"]
    from tools.build_world import pack_name
    assert ns == pack_name(circ["width"]).lower(), \
        "the page would name the pack something the README's /function is not"

    w = build()
    checked = 0
    for b in w.blocks.values():
        meta = 0
        if b.kind == "repeater":
            meta = DIRS6.index(b.facing) | ((b.delay - 1) << 3)
        elif b.kind == "comparator":
            meta = DIRS6.index(b.facing) | ((1 if b.mode == "subtract" else 0) << 3)
        elif b.kind == "redstone_torch":
            meta = DIRS6.index(b.attach)
        elif b.kind == "lever":
            meta = DIRS6.index(b.attach) | ((1 if b.on else 0) << 3)
        key = f"{KIND_ID[b.kind]}:{meta}"
        if key not in mc["map"]:
            continue                      # this combination is not in the Mk III
        name, props = mcbuild.block_state(b)
        want = name + ("" if not props else
                       "[" + ",".join(f"{k}={v}" for k, v in sorted(props.items()))
                       + "]")
        got = mc["palette"][mc["map"][key]]
        assert got == want, f"{b.kind}/{meta}: page says {got}, mcbuild says {want}"
        checked += 1
    assert checked >= 5, f"only {checked} kinds were comparable"
    print(f"  the page's {len(mc['palette'])}-entry Minecraft table agrees with "
          f"mcbuild on every one of {checked} kinds checked: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} export tests\n")
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
