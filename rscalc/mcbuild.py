"""Turn a placed World into something Minecraft can actually load.

Two vanilla routes, no mods needed for either:

* **Structure files** (``.nbt``) — what a structure block loads. Structure
  blocks top out at 48 blocks per side, so a machine of this size comes out as
  a grid of chunks plus a manifest saying where each one goes.
* **A datapack** of ``.mcfunction`` files — ``/setblock`` and ``/fill``
  commands, split into batches small enough to run without stalling the server.
  Runs of identical blocks along X are collapsed into one ``/fill``, which is
  what makes this affordable: most of the machine is the solid floor under the
  dust.

Getting the orientations right matters more than anything else here, because a
wrong convention fails silently — the build looks perfect and computes nothing:

* A repeater's ``facing`` in Minecraft is *the direction from its output side to
  its input side* — the opposite of this simulator's convention, where facing is
  the direction the signal leaves. Comparators are the same. Both are flipped on
  the way out.
* A redstone torch attached to the block below it is the standing
  ``redstone_torch``; attached to a wall it is ``redstone_wall_torch``, whose
  ``facing`` points *away* from the wall — again the opposite of this
  simulator's ``attach``.
* Redstone dust is written with default connections. The game recomputes those
  from the neighbours on the block update that placement triggers, so writing
  them here would only be a chance to get them wrong.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import struct

#: Java Edition 1.21. Any version at or below the world's own works — Minecraft
#: runs its data fixers on load — so an older number is the safe default.
DATA_VERSION = 3953
STRUCTURE_MAX = 48          # a structure block's limit, per side
DEFAULT_SOLID = "minecraft:light_gray_concrete"
#: 1.21's datapack format, in one place so the page and the
#: exporter cannot disagree about it
PACK_FORMAT_DEFAULT = 48

OPPOSITE = {"north": "south", "south": "north",
            "east": "west", "west": "east",
            "up": "down", "down": "up"}


# --- NBT writing ------------------------------------------------------------
# Just enough of the format to write a structure file: big-endian, every tag
# prefixed by its type and name.

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_STRING, TAG_LIST, TAG_COMPOUND = (
    0, 1, 2, 3, 8, 9, 10)


def _name(buf, tag, name):
    buf.write(struct.pack(">B", tag))
    raw = name.encode("utf-8")
    buf.write(struct.pack(">H", len(raw)))
    buf.write(raw)


def _string_payload(buf, s):
    raw = s.encode("utf-8")
    buf.write(struct.pack(">H", len(raw)))
    buf.write(raw)


def _int_list_payload(buf, values):
    buf.write(struct.pack(">B", TAG_INT))
    buf.write(struct.pack(">i", len(values)))
    for v in values:
        buf.write(struct.pack(">i", v))


def _block_state_payload(buf, name, props):
    """A compound: {Name: "...", Properties: {...}}"""
    _name(buf, TAG_STRING, "Name")
    _string_payload(buf, name)
    if props:
        _name(buf, TAG_COMPOUND, "Properties")
        for k, v in sorted(props.items()):
            _name(buf, TAG_STRING, k)
            _string_payload(buf, str(v))
        buf.write(struct.pack(">B", TAG_END))
    buf.write(struct.pack(">B", TAG_END))


def write_structure(path, size, palette, blocks, data_version=DATA_VERSION):
    """blocks: [(state_index, (x, y, z))], coordinates local to the structure."""
    buf = io.BytesIO()
    buf.write(struct.pack(">B", TAG_COMPOUND))      # unnamed root
    buf.write(struct.pack(">H", 0))

    _name(buf, TAG_INT, "DataVersion")
    buf.write(struct.pack(">i", data_version))

    _name(buf, TAG_LIST, "size")
    _int_list_payload(buf, list(size))

    _name(buf, TAG_LIST, "palette")
    buf.write(struct.pack(">B", TAG_COMPOUND))
    buf.write(struct.pack(">i", len(palette)))
    for name, props in palette:
        _block_state_payload(buf, name, props)

    _name(buf, TAG_LIST, "blocks")
    buf.write(struct.pack(">B", TAG_COMPOUND))
    buf.write(struct.pack(">i", len(blocks)))
    for state, pos in blocks:
        _name(buf, TAG_LIST, "pos")
        _int_list_payload(buf, list(pos))
        _name(buf, TAG_INT, "state")
        buf.write(struct.pack(">i", state))
        buf.write(struct.pack(">B", TAG_END))

    _name(buf, TAG_LIST, "entities")
    buf.write(struct.pack(">B", TAG_COMPOUND))
    buf.write(struct.pack(">i", 0))

    buf.write(struct.pack(">B", TAG_END))           # close the root
    raw = buf.getvalue()
    with gzip.GzipFile(path, "wb", mtime=0) as f:
        f.write(raw)
    return len(raw)


# --- block translation ------------------------------------------------------

def block_state(b, solid=DEFAULT_SOLID):
    """One of this simulator's blocks -> (minecraft id, properties)."""
    k = b.kind
    if k == "solid":
        return solid, {}
    if k == "glass":
        return "minecraft:glass", {}
    if k == "redstone_block":
        return "minecraft:redstone_block", {}
    if k == "lamp":
        return "minecraft:redstone_lamp", {"lit": "false"}
    if k == "redstone_wire":
        # connections and power are recomputed by the game on placement
        return "minecraft:redstone_wire", {}
    if k == "redstone_torch":
        if b.attach == "down":
            return "minecraft:redstone_torch", {"lit": "true"}
        # a wall torch points away from what it is attached to
        return "minecraft:redstone_wall_torch", {
            "facing": OPPOSITE[b.attach], "lit": "true"}
    if k == "repeater":
        # Minecraft's facing runs output -> input; ours runs the other way
        return "minecraft:repeater", {
            "facing": OPPOSITE[b.facing], "delay": str(b.delay),
            "locked": "false", "powered": "false"}
    if k == "comparator":
        return "minecraft:comparator", {
            "facing": OPPOSITE[b.facing], "mode": b.mode, "powered": "false"}
    if k == "lever":
        face = {"down": "floor", "up": "ceiling"}.get(b.attach, "wall")
        props = {"face": face, "powered": "true" if b.on else "false",
                 "facing": "north" if face != "wall" else OPPOSITE[b.attach]}
        return "minecraft:lever", props
    raise ValueError(f"no Minecraft block for kind {k!r}")


def palette_of(world, solid=DEFAULT_SOLID):
    """Distinct block states, and a lookup from position to palette index."""
    palette, index, state_of = [], {}, {}
    for pos, b in world.blocks.items():
        name, props = block_state(b, solid)
        key = (name, tuple(sorted(props.items())))
        if key not in index:
            index[key] = len(palette)
            palette.append((name, props))
        state_of[pos] = index[key]
    return palette, state_of


# --- structure export -------------------------------------------------------

def export_structures(world, outdir, name="rscalc", solid=DEFAULT_SOLID,
                      chunk=STRUCTURE_MAX, data_version=DATA_VERSION):
    """Write the world as a grid of structure files plus a manifest."""
    os.makedirs(outdir, exist_ok=True)
    (x0, y0, z0), (x1, y1, z1) = world.bounds()
    palette, state_of = palette_of(world, solid)

    cells = {}
    for pos in world.blocks:
        c = ((pos[0] - x0) // chunk, (pos[1] - y0) // chunk,
             (pos[2] - z0) // chunk)
        cells.setdefault(c, []).append(pos)

    manifest = {
        "name": name,
        "data_version": data_version,
        "origin": [x0, y0, z0],
        "size": [x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1],
        "blocks": len(world.blocks),
        "chunk": chunk,
        "pieces": [],
    }
    for c in sorted(cells):
        cx, cy, cz = c
        base = (x0 + cx * chunk, y0 + cy * chunk, z0 + cz * chunk)
        local = [(state_of[p], (p[0] - base[0], p[1] - base[1], p[2] - base[2]))
                 for p in cells[c]]
        size = [max(l[1][i] for l in local) + 1 for i in range(3)]
        fname = f"{name}_{cx}_{cy}_{cz}.nbt"
        write_structure(os.path.join(outdir, fname), size, palette, local,
                        data_version)
        manifest["pieces"].append({
            "file": fname,
            "offset": [cx * chunk, cy * chunk, cz * chunk],
            "size": size,
            "blocks": len(local),
        })
    with open(os.path.join(outdir, f"{name}_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


# --- datapack export --------------------------------------------------------

def _runs(world, state_of):
    """Collapse adjacent identical blocks into X runs, so most of this is /fill.

    Any kind can merge, because the palette entry carries the block's *whole*
    state — orientation, delay, mode — so two neighbours with the same palette
    index really are the same block. Rows are emitted in ascending Y, which
    matters: dust and torches need their support to exist already, and the
    support is the row beneath.
    """
    def scan(rows, make):
        """Merge along one axis. Yields (state, a, b) and leftover singletons."""
        for key, cs in sorted(rows.items()):
            cs.sort()
            start = prev = cs[0]
            state = state_of[make(key, start)]
            for c in cs[1:]:
                st = state_of[make(key, c)]
                if c == prev + 1 and st == state:
                    prev = c
                    continue
                yield state, make(key, start), make(key, prev)
                start, prev, state = c, c, st
            yield state, make(key, start), make(key, prev)

    # Rails run along X and collectors along Z, so neither axis alone catches
    # much: merge along X first, then take everything that stayed a singleton
    # and try again along Z. Two passes get most of the win for very little
    # bookkeeping.
    rows_x = {}
    for pos in world.blocks:
        rows_x.setdefault((pos[1], pos[2]), []).append(pos[0])
    singles, out = [], []
    for state, a, b in scan(rows_x, lambda k, c: (c, k[0], k[1])):
        if a == b:
            singles.append((state, a))
        else:
            out.append((state, a, b))

    rows_z = {}
    for state, pos in singles:
        rows_z.setdefault((pos[1], pos[0]), []).append(pos[2])
    for state, a, b in scan(rows_z, lambda k, c: (k[1], k[0], c)):
        out.append((state, a, b))

    # ascending Y, because dust and torches need the row beneath them to exist
    out.sort(key=lambda r: (r[1][1], r[1][2], r[1][0]))
    yield from out


def _command(name, props, a, b):
    state = name
    if props:
        state += "[" + ",".join(f"{k}={v}" for k, v in sorted(props.items())) + "]"
    if a == b:
        return f"setblock ~{a[0]} ~{a[1]} ~{a[2]} {state} replace"
    return (f"fill ~{a[0]} ~{a[1]} ~{a[2]} ~{b[0]} ~{b[1]} ~{b[2]} "
            f"{state} replace")


def export_datapack(world, outdir, name="rscalc", solid=DEFAULT_SOLID,
                    per_file=2000, pack_format=PACK_FORMAT_DEFAULT):
    """A datapack whose functions rebuild the machine relative to the player.

    Commands are relative (``~``), so running the entry function places the
    machine from wherever it is executed. They are split across many functions
    because a single one with half a million commands would stall the server for
    minutes; the entry function calls them in order.
    """
    ns = name.lower()
    fdir = os.path.join(outdir, "data", ns, "function")
    # Clear it first. Without this an export inherits every `partNNNN` a
    # previous, differently-chunked run left behind: the shipped pack held
    # part0009 and part0010 from two earlier builds, 512 KB of another
    # machine's commands, uncalled but distributed.
    if os.path.isdir(fdir):
        for stale in os.listdir(fdir):
            if stale.endswith(".mcfunction"):
                os.remove(os.path.join(fdir, stale))
    os.makedirs(fdir, exist_ok=True)
    (x0, y0, z0), _ = world.bounds()
    palette, state_of = palette_of(world, solid)

    lines, files, count = [], [], 0
    def flush():
        if not lines:
            return
        idx = len(files)
        fn = f"part{idx:04d}"
        with open(os.path.join(fdir, f"{fn}.mcfunction"), "w") as f:
            f.write("\n".join(lines) + "\n")
        files.append(fn)
        lines.clear()

    for st, a, b in _runs(world, state_of):
        pname, props = palette[st]
        la = (a[0] - x0, a[1] - y0, a[2] - z0)
        lb = (b[0] - x0, b[1] - y0, b[2] - z0)
        lines.append(_command(pname, props, la, lb))
        count += 1
        if len(lines) >= per_file:
            flush()
    flush()

    (bx0, by0, bz0), (bx1, by1, bz1) = world.bounds()
    paced = write_paced_entry(fdir, ns, name, files, count,
                              (bx1 - bx0 + 1, by1 - by0 + 1, bz1 - bz0 + 1))
    with open(os.path.join(outdir, "pack.mcmeta"), "w") as f:
        json.dump({"pack": {"pack_format": pack_format,
                            "description": f"{name} — a redstone calculator"}},
                  f, indent=1)
    return {"commands": count, "files": len(os.listdir(fdir)), **paced}


def _write(path, lines):
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_paced_entry(fdir, ns, name, files, count, dims):
    """The entry point, spread over ticks instead of run in one.

    The obvious entry function calls every part in a row, and that is what this
    wrote first: 71,768 commands in a single game tick. A server executes all of
    it before the tick ends, and every block of a half-million-block redstone
    machine appears in the same instant — the most hostile possible starting
    transient for the thing §13 spends a table on.

    So the parts run one per tick, driven by a `schedule` chain. That has a trap
    in it which is not obvious: **a scheduled function does not remember where
    it was called from.** It executes at the world origin with no executor, so
    every `~` in the commands — all of them, because the build is relative —
    would place the machine at 0, 0, 0 instead of at the player.

    The fix is the standard one: drop a `marker` where the build was started and
    run each part `as` that marker `at` its position, so the origin rides
    through the chain. A scoreboard holds which part is next, which keeps the
    parts themselves pure command lists — that is what lets the replay test read
    them without interpreting any control flow.
    """
    obj, tag = f"{ns}_step", f"{ns}_origin"
    dx, dy, dz = dims

    _write(os.path.join(fdir, "build.mcfunction"), [
        f"# {count:,} commands in {len(files)} parts, one part per tick.",
        f"# Stand at the machine's -X -Y -Z corner and run this.",
        f"scoreboard objectives add {obj} dummy",
        f"kill @e[type=marker,tag={tag}]",
        f'summon marker ~ ~ ~ {{Tags:["{tag}"]}}',
        f"scoreboard players set #build {obj} 0",
        f'tellraw @a {{"text":"{name}: {count} commands over {len(files)} '
        f'ticks...","color":"gray"}}',
        f"function {ns}:tick",
    ])

    _write(os.path.join(fdir, "tick.mcfunction"), [
        f"execute as @e[type=marker,tag={tag},limit=1] at @s "
        f"run function {ns}:dispatch",
        f"scoreboard players add #build {obj} 1",
        f"execute if score #build {obj} matches ..{len(files) - 1} run "
        f"schedule function {ns}:tick 1t replace",
        f"execute if score #build {obj} matches {len(files)}.. run "
        f"function {ns}:done",
    ])

    # `execute if score` rather than a macro, so this runs on any 1.21 build
    _write(os.path.join(fdir, "dispatch.mcfunction"),
           [f"execute if score #build {obj} matches {i} run function {ns}:{fn}"
            for i, fn in enumerate(files)])

    _write(os.path.join(fdir, "done.mcfunction"), [
        f"kill @e[type=marker,tag={tag}]",
        f"scoreboard objectives remove {obj}",
        f'tellraw @a {{"text":"{name} placed. The control wall is at the '
        f'-X end.","color":"green"}}',
    ])

    # --- and a way back out ---------------------------------------------
    # A half-million-block machine in the wrong place is not something anyone
    # should dig out by hand. `fill` caps at 32,768 blocks a command, and one Y
    # layer here is 555 x 973, so the layer is cut into Z strips that fit; the
    # marker climbs one layer per tick, which paces the removal exactly the way
    # the build is paced.
    zstep = max(1, 32768 // max(1, dx))
    strips = [f"fill ~ ~ ~{z} ~{dx - 1} ~ ~{min(dz - 1, z + zstep - 1)} "
              f"minecraft:air replace"
              for z in range(0, dz, zstep)]

    _write(os.path.join(fdir, "clear.mcfunction"), [
        f"# Run from the same corner `build` was run from.",
        f"scoreboard objectives add {obj}c dummy",
        f"kill @e[type=marker,tag={tag}c]",
        f'summon marker ~ ~ ~ {{Tags:["{tag}c"]}}',
        f"scoreboard players set #clear {obj}c 0",
        f'tellraw @a {{"text":"{name}: clearing {dy} layers...",'
        f'"color":"gray"}}',
        f"function {ns}:clear_tick",
    ])
    _write(os.path.join(fdir, "clear_tick.mcfunction"), [
        f"execute as @e[type=marker,tag={tag}c,limit=1] at @s "
        f"run function {ns}:clear_slice",
        f"execute as @e[type=marker,tag={tag}c,limit=1] at @s "
        f"run tp @s ~ ~1 ~",
        f"scoreboard players add #clear {obj}c 1",
        f"execute if score #clear {obj}c matches ..{dy - 1} run "
        f"schedule function {ns}:clear_tick 1t replace",
        f"execute if score #clear {obj}c matches {dy}.. run "
        f"function {ns}:clear_done",
    ])
    _write(os.path.join(fdir, "clear_slice.mcfunction"), strips)
    _write(os.path.join(fdir, "clear_done.mcfunction"), [
        f"kill @e[type=marker,tag={tag}c]",
        f"scoreboard objectives remove {obj}c",
        f'tellraw @a {{"text":"{name} removed.","color":"gray"}}',
    ])
    return {"entry": f"function {ns}:build", "clear": f"function {ns}:clear",
            "ticks": len(files), "clear_ticks": dy,
            "fills_per_layer": len(strips)}
