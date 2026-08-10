"""The Minecraft build output: does it describe the machine we simulated?

A build export that is subtly wrong is worse than none — it looks right in the
world and computes nothing. So these tests read the files back rather than
trusting the writer: the structure files are parsed with an independent NBT
reader written here, and the datapack's commands are replayed into a fresh
world which is then compared block for block against the original.

The orientation conventions get their own test, because they are where a silent
failure would come from: Minecraft's repeater `facing` runs from the output side
to the input side, the opposite of this simulator's.
"""

import sys, os, gzip, struct, re, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World
from rscalc.netlist import Netlist
from rscalc.pla import compile_netlist
from rscalc import mcbuild


# --- a small, independent NBT reader, so the writer is not marking its own work

def _read_nbt(path):
    with gzip.open(path, "rb") as f:
        data = f.read()
    pos = 0

    def u1():
        nonlocal pos
        v = data[pos]; pos += 1; return v

    def i2():
        nonlocal pos
        v = struct.unpack_from(">h", data, pos)[0]; pos += 2; return v

    def i4():
        nonlocal pos
        v = struct.unpack_from(">i", data, pos)[0]; pos += 4; return v

    def name():
        nonlocal pos
        n = struct.unpack_from(">H", data, pos)[0]; pos += 2
        s = data[pos:pos + n].decode("utf-8"); pos += n
        return s

    def payload(tag):
        nonlocal pos
        if tag == 1: return u1()
        if tag == 2: return i2()
        if tag == 3: return i4()
        if tag == 8: return name()
        if tag == 9:
            et = u1(); n = i4()
            return [payload(et) for _ in range(n)]
        if tag == 10:
            out = {}
            while True:
                t = u1()
                if t == 0:
                    return out
                # the name has to be read before the payload: in `d[k] = v`
                # Python evaluates v first, which would consume the stream in
                # the wrong order
                key = name()
                out[key] = payload(t)
        raise ValueError(f"unsupported tag {tag}")

    root_tag = u1()
    assert root_tag == 10, "root must be a compound"
    assert name() == "", "root must be unnamed"
    out = payload(10)
    assert pos == len(data), f"{len(data) - pos} trailing bytes"
    return out


def _sample_world():
    """A small compiled circuit: one of everything the exporter has to handle."""
    nl = Netlist()
    a, b = nl.input("A"), nl.input("B")
    nl.output("X", nl.xor(a, b))
    nl.output("N", nl.not_(a))
    w = World()
    compile_netlist(nl, w, repeater_delay=2)
    w.lamp((0, 40, 0))
    w.solid((0, 39, 0))
    w.comparator((2, 40, 0), facing="east", mode="subtract")
    w.solid((2, 39, 0))
    return w


def test_orientation_conventions():
    """Facings must be flipped on the way out, or the build computes nothing."""
    w = World()
    w.solid((0, -1, 0))
    w.repeater((0, 0, 0), facing="east", delay=3)
    w.solid((1, -1, 0))
    w.comparator((1, 0, 0), facing="north", mode="subtract")
    w.solid((2, 0, 0))
    w.torch((2, 1, 0), attach="down")
    w.solid((3, 0, 0))
    w.torch((4, 0, 0), attach="west")

    got = {p: mcbuild.block_state(b) for p, b in w.blocks.items()}
    name, props = got[(0, 0, 0)]
    assert name == "minecraft:repeater", name
    assert props["facing"] == "west", (
        "a repeater whose signal leaves east has facing=west in Minecraft, "
        f"got {props['facing']}")
    assert props["delay"] == "3", props
    name, props = got[(1, 0, 0)]
    assert name == "minecraft:comparator" and props["facing"] == "south", props
    assert props["mode"] == "subtract", props
    name, props = got[(2, 1, 0)]
    assert name == "minecraft:redstone_torch", name
    name, props = got[(4, 0, 0)]
    assert name == "minecraft:redstone_wall_torch", name
    assert props["facing"] == "east", (
        "a torch attached to the wall on its west points east, "
        f"got {props['facing']}")
    print("  orientations: repeater and comparator facings flip, wall torches "
          "point away from their support: OK")


def test_structure_roundtrip():
    """Write structure chunks, read them back, rebuild, compare block by block."""
    w = _sample_world()
    with tempfile.TemporaryDirectory() as d:
        man = mcbuild.export_structures(w, d, name="t", chunk=16)
        assert man["blocks"] == len(w.blocks)
        assert sum(p["blocks"] for p in man["pieces"]) == len(w.blocks), (
            "chunks must cover every block exactly once")

        rebuilt = {}
        for piece in man["pieces"]:
            nbt = _read_nbt(os.path.join(d, piece["file"]))
            assert nbt["DataVersion"] == mcbuild.DATA_VERSION
            assert nbt["size"] == piece["size"]
            pal = nbt["palette"]
            for blk in nbt["blocks"]:
                x, y, z = blk["pos"]
                ox, oy, oz = piece["offset"]
                key = (x + ox, y + oy, z + oz)
                assert key not in rebuilt, f"{key} written twice"
                st = pal[blk["state"]]
                rebuilt[key] = (st["Name"], st.get("Properties", {}))

        (x0, y0, z0), _ = w.bounds()
        assert len(rebuilt) == len(w.blocks)
        for pos, b in w.blocks.items():
            key = (pos[0] - x0, pos[1] - y0, pos[2] - z0)
            want_name, want_props = mcbuild.block_state(b)
            got_name, got_props = rebuilt[key]
            assert got_name == want_name, (pos, got_name, want_name)
            assert got_props == {k: str(v) for k, v in want_props.items()}, pos
    print(f"  structure files: {len(w.blocks)} blocks over {len(man['pieces'])} "
          f"chunks, parsed back and matched exactly: OK")


def test_datapack_replays_to_the_same_world():
    """Replay the generated commands and demand an identical world."""
    w = _sample_world()
    with tempfile.TemporaryDirectory() as d:
        info = mcbuild.export_datapack(w, d, name="t", per_file=500)
        fdir = os.path.join(d, "data", "t", "function")
        parts = sorted(f for f in os.listdir(fdir) if f.startswith("part"))
        assert parts, "no command files written"

        setb = re.compile(r"^setblock ~(-?\d+) ~(-?\d+) ~(-?\d+) (\S+) replace$")
        fill = re.compile(r"^fill ~(-?\d+) ~(-?\d+) ~(-?\d+) "
                          r"~(-?\d+) ~(-?\d+) ~(-?\d+) (\S+) replace$")
        placed, n_cmds = {}, 0
        for part in parts:
            for line in open(os.path.join(fdir, part)):
                line = line.strip()
                if not line:
                    continue
                n_cmds += 1
                m = setb.match(line)
                if m:
                    x, y, z = (int(m.group(i)) for i in (1, 2, 3))
                    placed[(x, y, z)] = m.group(4)
                    continue
                m = fill.match(line)
                assert m, f"unparseable command: {line}"
                a = [int(m.group(i)) for i in (1, 2, 3)]
                b = [int(m.group(i)) for i in (4, 5, 6)]
                # runs merge along X first and then along Z, so a fill is a
                # straight line on exactly one axis — never a box
                varying = [i for i in range(3) if a[i] != b[i]]
                assert len(varying) <= 1, f"fills must be lines, not boxes: {line}"
                if not varying:
                    placed[tuple(a)] = m.group(7)
                else:
                    ax = varying[0]
                    for c in range(min(a[ax], b[ax]), max(a[ax], b[ax]) + 1):
                        q = list(a); q[ax] = c
                        placed[tuple(q)] = m.group(7)
        assert n_cmds == info["commands"], (n_cmds, info["commands"])

        (x0, y0, z0), _ = w.bounds()
        assert len(placed) == len(w.blocks), (len(placed), len(w.blocks))
        for pos, b in w.blocks.items():
            key = (pos[0] - x0, pos[1] - y0, pos[2] - z0)
            name, props = mcbuild.block_state(b)
            want = name
            if props:
                want += "[" + ",".join(f"{k}={v}" for k, v in sorted(props.items())) + "]"
            assert placed[key] == want, (pos, placed[key], want)

        assert os.path.exists(os.path.join(d, "pack.mcmeta"))
    ratio = len(w.blocks) / max(1, info["commands"])
    print(f"  datapack: {info['commands']} commands rebuild all "
          f"{len(w.blocks)} blocks exactly ({ratio:.1f} blocks per command): OK")


def test_every_block_kind_has_a_mapping():
    """Anything the compiler can place must be exportable."""
    from rscalc.engine import Block
    kinds = ["solid", "glass", "redstone_block", "lamp", "redstone_wire",
             "redstone_torch", "repeater", "comparator", "lever"]
    for k in kinds:
        name, props = mcbuild.block_state(Block(k))
        assert name.startswith("minecraft:"), (k, name)
    try:
        mcbuild.block_state(Block("piston"))
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown kind must fail loudly, not silently")
    print(f"  block mapping: all {len(kinds)} kinds map to Minecraft ids, "
          f"unknown kinds raise: OK")


def _chain(fdir, ns):
    """Follow build -> tick -> dispatch the way the game would.

    A tiny interpreter for the four commands this pack's control flow uses:
    `scoreboard players set/add`, `execute if score ... matches ... run
    function`, and `schedule function`. It exists because the pacing is real
    logic — a counter, a dispatch table and a self-rescheduling tick — and
    nothing else here reads it.
    """
    import re as _re
    def load(fn):
        return [l.strip() for l in
                open(os.path.join(fdir, fn + ".mcfunction")) if l.strip()
                and not l.startswith("#")]

    score, ran, ticks, guard = {}, [], 0, 0
    pending = "tick"
    setre = _re.compile(r"scoreboard players set (\S+) (\S+) (-?\d+)")
    addre = _re.compile(r"scoreboard players add (\S+) (\S+) (-?\d+)")
    ifre = _re.compile(r"execute if score (\S+) (\S+) matches "
                       r"(\.\.-?\d+|-?\d+\.\.|-?\d+) run (.+)")

    for line in load("build"):
        m = setre.match(line)
        if m:
            score[(m.group(1), m.group(2))] = int(m.group(3))

    def matches(spec, v):
        if spec.startswith(".."):
            return v <= int(spec[2:])
        if spec.endswith(".."):
            return v >= int(spec[:-2])
        return v == int(spec)

    while pending and guard < 5000:
        guard += 1
        fn, pending = pending, None
        ticks += 1
        for line in load(fn):
            m = ifre.match(line)
            if m:
                key = (m.group(1), m.group(2))
                if not matches(m.group(3), score.get(key, 0)):
                    continue
                rest = m.group(4)
            else:
                rest = line
            if rest.startswith("execute as @e") and "run function" in rest:
                rest = "function " + rest.split("run function ", 1)[1]
            if rest.startswith("function "):
                target = rest.split()[1].split(":")[1]
                if target == "dispatch":
                    for d in load("dispatch"):
                        md = ifre.match(d)
                        if md and matches(md.group(3),
                                          score.get((md.group(1), md.group(2)), 0)):
                            ran.append(md.group(4).split()[1].split(":")[1])
                elif target == "done":
                    ran.append("done")
                elif target == "tick":
                    pending = "tick"
            elif rest.startswith("schedule function "):
                pending = rest.split()[2].split(":")[1]
            m = addre.match(rest)
            if m:
                key = (m.group(1), m.group(2))
                score[key] = score.get(key, 0) + int(m.group(3))
    return ran, ticks


def test_the_build_is_paced_over_ticks_and_not_run_in_one():
    """Every part exactly once, in order, one per tick, then `done`.

    The first version of this entry function called all nine parts in a row:
    71,768 commands inside a single game tick, and every block of a
    half-million-block redstone machine appearing in the same instant.
    """
    w = _sample_world()
    with tempfile.TemporaryDirectory() as d:
        info = mcbuild.export_datapack(w, d, name="t", per_file=40)
        fdir = os.path.join(d, "data", "t", "function")
        parts = sorted(f[:-11] for f in os.listdir(fdir) if f.startswith("part"))
        ran, ticks = _chain(fdir, "t")

        assert ran[-1] == "done", f"the chain never finished: {ran[-3:]}"
        placed = [r for r in ran if r != "done"]
        assert placed == parts, (f"the chain runs {placed}, the pack holds "
                                 f"{parts}")
        assert ticks >= len(parts), \
            f"{len(parts)} parts placed in {ticks} ticks — that is not paced"
        print(f"  the build runs {len(parts)} parts over {ticks} ticks, each "
              f"exactly once, then reports done: OK")


def test_no_function_in_the_pack_is_unreachable():
    """The exporter used to leave `partNNNN` files from earlier, differently
    chunked runs in the directory — the shipped pack carried two of them, from
    two different machines, uncalled but distributed."""
    w = _sample_world()
    with tempfile.TemporaryDirectory() as d:
        info = mcbuild.export_datapack(w, d, name="t", per_file=40)
        fdir = os.path.join(d, "data", "t", "function")
        on_disk = {f[:-11] for f in os.listdir(fdir)}
        # seeded from what the exporter *declares* as entry points, so a
        # function can only be excused by being advertised to the player
        called = {v.split(":")[1] for k, v in info.items()
                  if isinstance(v, str) and v.startswith("function ")}
        for fn in list(on_disk):
            for line in open(os.path.join(fdir, fn + ".mcfunction")):
                for token in line.split():
                    if ":" in token and token.split(":")[0] == "t":
                        called.add(token.split(":")[1])
        orphans = on_disk - called
        assert not orphans, (f"functions nothing calls and nothing advertises: "
                             f"{sorted(orphans)}")
        entries = {v for v in info.values()
                   if isinstance(v, str) and v.startswith("function ")}
        print(f"  all {len(on_disk)} functions are reachable from the "
              f"{len(entries)} advertised entry points: OK")


def test_the_declared_control_functions_are_the_ones_written():
    """`CONTROL_FUNCTIONS` is what the page is told to generate.

    It is shipped in the bundle so the browser's copy of this generator can be
    checked against it — which is only worth anything if the list matches what
    *this* file actually writes. A constant that has drifted from its own
    exporter would hand the page a wrong answer with full confidence.
    """
    w = _sample_world()
    with tempfile.TemporaryDirectory() as d:
        mcbuild.export_datapack(w, d, name="t", per_file=40)
        fdir = os.path.join(d, "data", "t", "function")
        written = {f[:-11] for f in os.listdir(fdir)
                   if f.endswith(".mcfunction") and not f.startswith("part")}
        assert written == set(mcbuild.CONTROL_FUNCTIONS), (
            f"declared {sorted(mcbuild.CONTROL_FUNCTIONS)}, "
            f"wrote {sorted(written)}")
        print(f"  all {len(written)} declared control functions are written, "
              f"and nothing else is: OK")


def test_the_whole_footprint_is_force_loaded():
    """Redstone only ticks in chunks the game is simulating.

    This is the limitation that decides whether the machine works in a real
    world at all, and it is invisible: at Java's default simulation distance a
    player sees a 21 x 21 square of chunks, and the Mk III is 35 x 61 of them.
    Throw a lever at the control wall and the far end is frozen — no error, no
    sign, the answer just never arrives.

    `/forceload add` takes at most 256 chunks a command, so the cover has to be
    tiled. Both halves matter: every chunk of the footprint inside some tile,
    and no tile over the limit.
    """
    w = _sample_world()
    with tempfile.TemporaryDirectory() as d:
        info = mcbuild.export_datapack(w, d, name="t", per_file=40)
        fdir = os.path.join(d, "data", "t", "function")
        (x0, y0, z0), (x1, y1, z1) = w.bounds()
        dx, dz = x1 - x0 + 1, z1 - z0 + 1

        got = set()
        for line in open(os.path.join(fdir, "load.mcfunction")):
            m = re.match(r"forceload add ~(-?\d+) ~(-?\d+) ~(-?\d+) ~(-?\d+)",
                         line.strip())
            if not m:
                continue
            a, b, c, e = (int(m.group(i)) for i in (1, 2, 3, 4))
            chunks = ((c // 16) - (a // 16) + 1) * ((e // 16) - (b // 16) + 1)
            assert chunks <= 256, f"one forceload covers {chunks} chunks, max 256"
            for cx in range(a // 16, c // 16 + 1):
                for cz in range(b // 16, e // 16 + 1):
                    got.add((cx, cz))
        want = {(cx, cz)
                for cx in range((dx - 1) // 16 + 1)
                for cz in range((dz - 1) // 16 + 1)}
        assert want <= got, f"{len(want - got)} chunks of the machine are " \
                            f"never force-loaded, e.g. {sorted(want - got)[:3]}"

        # and every one is released again
        rem = {l.strip().replace("remove", "add")
               for l in open(os.path.join(fdir, "unload.mcfunction"))
               if l.startswith("forceload remove")}
        add = {l.strip() for l in open(os.path.join(fdir, "load.mcfunction"))
               if l.startswith("forceload add")}
        assert rem == add, "unload does not undo exactly what load does"
        print(f"  {len(want)} chunks of footprint covered by "
              f"{info['forceload_commands']} forceload commands, none over "
              f"256, and unload undoes each one: OK")


def test_a_third_party_reader_agrees_about_the_structures():
    """The reader above was written here, beside the writer.

    That is the same author checking their own understanding of the NBT spec
    twice, which is exactly the gap §14 opened for the redstone rules. `nbtlib`
    is somebody else's implementation; if it disagrees, one of us is wrong about
    the format rather than about this file.
    """
    try:
        import nbtlib
    except ImportError:
        print("  (nbtlib not installed — see requirements-dev.txt; skipped)")
        return
    w = _sample_world()
    with tempfile.TemporaryDirectory() as d:
        man = mcbuild.export_structures(w, d, name="t", chunk=8)
        checked = 0
        for piece in man["pieces"]:
            f = nbtlib.load(os.path.join(d, piece["file"]))
            root = f if "size" in f else f[""]
            assert [int(v) for v in root["size"]] == piece["size"], piece["file"]
            assert int(root["DataVersion"]) == mcbuild.DATA_VERSION
            mine = _read_nbt(os.path.join(d, piece["file"]))
            assert len(root["blocks"]) == len(mine["blocks"]), piece["file"]
            assert [str(p["Name"]) for p in root["palette"]] == \
                   [p["Name"] for p in mine["palette"]], piece["file"]
            for a, b in zip(root["blocks"], mine["blocks"]):
                assert [int(v) for v in a["pos"]] == b["pos"], piece["file"]
                assert int(a["state"]) == b["state"], piece["file"]
            checked += len(root["blocks"])
        print(f"  nbtlib reads back {checked} blocks over "
              f"{len(man['pieces'])} files and agrees with the reader here "
              f"on every one: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} build-output tests\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as ex:
            print(f"  FAIL {t.__name__}: {ex}"); failed += 1
        except Exception as ex:
            import traceback; traceback.print_exc()
            print(f"  ERROR {t.__name__}: {type(ex).__name__}: {ex}"); failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
