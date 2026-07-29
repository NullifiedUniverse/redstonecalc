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

from rscalc.engine import World, Engine
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
