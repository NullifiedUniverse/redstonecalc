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
#: Data pack formats, by the version string a player would recognise.
#:
#: Two things changed under this project's feet and both break a pack silently
#: rather than loudly. Mojang renamed the game itself — what a player calls
#: "1.26.2" the pack format table calls **26.2** — and since 25w31a the
#: `pack_format` integer is gone, replaced by `min_format`/`max_format` written
#: as `[major, minor]` pairs. A pack carrying only the old field is not read as
#: "close enough"; it is read as a pack for a version that no longer exists.
#:
#: Values are (major, minor). Anything at or above 88 gets the new fields; the
#: older entries stay because they are what a 1.21 world still wants.
MC_FORMATS = {
    "1.21":   (48, 0),
    "1.21.9": (88, 0),
    "1.21.11": (94, 1),
    "26.1":   (101, 1),
    "26.2":   (107, 1),
}
#: What this project targets. The machine uses `/fill`, `/setblock`,
#: `/forceload`, `/schedule`, `/scoreboard`, `/execute`, `/summon marker` and
#: `/tellraw`, and the pack-format table records **no breaking change to any of
#: them** between 48 and 107 — so the same commands really do serve both. Only
#: the metadata had to move.
MC_VERSION_DEFAULT = "26.2"
#: The first format that speaks `min_format`/`max_format` instead of
#: `pack_format`.
MC_RANGE_FORMAT = 88
#: 1.21's datapack format, kept as the legacy fallback written alongside the
#: range so an older world still recognises the pack.
PACK_FORMAT_DEFAULT = 48


def pack_meta(description, version=MC_VERSION_DEFAULT):
    """The `pack.mcmeta` body for a target version.

    Both spellings go in. A 26.x client reads `min_format`/`max_format` and
    ignores the integer; a 1.21 client reads the integer and ignores the pair.
    Neither errors on the other's field, so one file serves both, and the range
    is opened down to 1.21 rather than pinned so a pack built today still loads
    on the version it was tested against.
    """
    if version not in MC_FORMATS:
        raise ValueError(f"unknown Minecraft version {version!r}; "
                         f"known: {', '.join(sorted(MC_FORMATS))}")
    major, minor = MC_FORMATS[version]
    pack = {"description": description}
    if major >= MC_RANGE_FORMAT:
        pack["min_format"] = list(MC_FORMATS["1.21"])
        pack["max_format"] = [major, minor]
        pack["pack_format"] = PACK_FORMAT_DEFAULT
    else:
        pack["pack_format"] = major
    return {"pack": pack}
#: Commands per `partNNNN` function, which is also what sets how many game ticks
#: the paced build takes. Named because the page prints the tick count in its
#: prose and has to arrive at the same one.
PER_FILE_DEFAULT = 2000

#: There are two generators of this pack — this file and the one in
#: `docs/preview_template.html` — and the first thing that went wrong with
#: having two was that force-loading went into one of them. A reader
#: downloading from the page got a pack whose machine ticks in a fifth of its
#: own chunks: §33's failure, silently.
#:
#: The list of control functions used to be duplicated here as a constant for
#: the page to check itself against. It is not any more, because the page does
#: not write them at all now — `export_datapack` returns their finished text in
#: `control_files` and the bundle carries it, so the browser copies bytes it
#: cannot get wrong. What is left to check is that the two agree, which
#: `tools/check_preview.mjs` does directly.

OPPOSITE = {"north": "south", "south": "north",
            "east": "west", "west": "east",
            "up": "down", "down": "up"}

#: Kinds that hold other blocks up — a full cube with a sturdy face. Everything
#: else in this project is a redstone component that breaks the instant its
#: support is missing, which is why the two go down in that order.
SUPPORT_KINDS = frozenset({"solid", "glass", "lamp", "redstone_block"})
#: Which neighbour each component needs, as an offset from its own position.
#: `None` means "read it off the block" — a torch or lever carries its `attach`.
NEEDS_SUPPORT = frozenset({"redstone_wire", "repeater", "comparator",
                           "redstone_torch", "lever"})


def support_of(pos, b):
    """Where the block holding `b` up has to be, or None if nothing holds it.

    Minecraft breaks a component the moment this neighbour stops being a sturdy
    face, and a command that places one into thin air does not fail — the block
    appears, the game updates it, and it drops as an item. A build of half a
    million blocks loses a few hundred that way and still looks finished.
    """
    if b.kind not in NEEDS_SUPPORT:
        return None
    attach = getattr(b, "attach", "down")
    if b.kind in ("redstone_torch", "lever") and attach != "down":
        # a wall torch hangs off the block it points away from; a ceiling lever
        # off the one above. Same layer or higher — not the row beneath.
        d = {"up": (0, 1, 0), "north": (0, 0, -1), "south": (0, 0, 1),
             "east": (1, 0, 0), "west": (-1, 0, 0)}[attach]
        return (pos[0] + d[0], pos[1] + d[1], pos[2] + d[2])
    return (pos[0], pos[1] - 1, pos[2])


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

    # Supports first, then everything that hangs off one.
    #
    # Ascending Y alone was *nearly* right and was right by luck. Dust, torches,
    # repeaters and floor levers all need the block beneath them, and the row
    # beneath is a lower Y, so sorting by Y put every support first — as long as
    # nothing ever attached **sideways**. `block_state` already emits
    # `redstone_wall_torch` and wall levers, whose support is a horizontal
    # neighbour at the *same* Y, and the tiebreak here is (z, x): a wall torch
    # whose wall sits at +X would have been placed against nothing, popped off
    # as an item, and left a hole in a machine too big to find it in.
    #
    # Sorting the whole skeleton ahead of the whole circuit costs nothing — runs
    # are homogeneous, so the merge and the command count are untouched — and it
    # turns a coincidence into an invariant `tests/test_mcbuild.py` can state:
    # no block is ever placed before the thing holding it up.
    def phase(run):
        return 0 if world.blocks[run[1]].kind in SUPPORT_KINDS else 1
    out.sort(key=lambda r: (phase(r), r[1][1], r[1][2], r[1][0]))
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
                    per_file=PER_FILE_DEFAULT,
                    mc_version=MC_VERSION_DEFAULT, landmarks=None):
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
                              (bx1 - bx0 + 1, by1 - by0 + 1, bz1 - bz0 + 1),
                              landmarks=landmarks)
    meta = pack_meta(f"{name} — a redstone calculator", mc_version)
    with open(os.path.join(outdir, "pack.mcmeta"), "w") as f:
        json.dump(meta, f, indent=1)
    return {"commands": count, "files": len(os.listdir(fdir)),
            "mc_version": mc_version, "pack_meta": meta, **paced}


def _write(path, lines):
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_paced_entry(fdir, ns, name, files, count, dims, landmarks=None):
    """Everything in the pack that is not a batch of blocks.

    The parts run one per tick rather than all at once: 71,768 commands inside a
    single game tick freezes a server and drops half a million redstone blocks
    into the world in the same instant, which is the most hostile possible
    starting transient for a machine 36 stages deep. A `schedule` chain paces
    them.

    That chain has a trap in it: **a scheduled function does not remember where
    it was called from.** It runs at the world origin with no executor, so every
    `~` — all of them, because the build is relative — would place the machine
    at 0, 0, 0. A `marker` entity carries the position through the chain.

    The marker is now an *anchor*: aligned to the block grid, written into
    `data storage`, and kept after the build finishes. Everything else in the
    pack reads it, which is what makes `clear` reliable. `clear` used to start
    from wherever the player happened to be standing and force-load nothing, so
    it cleared 195 layers of whatever was under their feet and stopped at the
    edge of the chunks the server was already simulating — the reason it never
    took the whole machine away.
    """
    obj, tag = f"{ns}_step", f"{ns}_anchor"
    store = f"{ns}:origin"
    dx, dy, dz = dims
    nparts = len(files)
    marks = {"origin": (0, 0, 0),
             "above": (dx // 2, dy + 12, dz // 2), **(landmarks or {})}
    out = {}

    def fn(fname, lines):
        out[fname] = list(lines)

    # --- placing it ---------------------------------------------------------
    fn("build", [
        f"# {count:,} commands in {nparts} parts, one part per tick.",
        f"# Stand at the machine's -X -Y -Z corner and run this.",
        f"scoreboard objectives add {obj} dummy",
        # A second build on top of a running one interleaves two schedule
        # chains through one scoreboard and places the machine twice, wrongly.
        f"execute if score #run {obj} matches 1 run function {ns}:busy",
        f"execute if score #run {obj} matches 1 run return fail",
        f"scoreboard players set #run {obj} 1",
        f"scoreboard players set #build {obj} 0",
        f"data remove storage {ns}:ui layer",
        f"kill @e[type=marker,tag={tag}]",
        # `align xyz` floors the position before summoning, so the anchor sits
        # exactly on the block corner. Without it the stored origin is the
        # player's fractional position, and truncating that towards zero is off
        # by one for every negative coordinate — a machine 555 blocks wide,
        # placed one block from where `clear` would later look for it.
        f'execute at @s align xyz run summon marker ~ ~ ~ {{Tags:["{tag}"]}}',
        f"execute store result storage {store} x int 1 run "
        f"data get entity @e[type=marker,tag={tag},limit=1] Pos[0]",
        f"execute store result storage {store} y int 1 run "
        f"data get entity @e[type=marker,tag={tag},limit=1] Pos[1]",
        f"execute store result storage {store} z int 1 run "
        f"data get entity @e[type=marker,tag={tag},limit=1] Pos[2]",
        f'tellraw @a {{"text":"{name}: {count:,} commands over {nparts} ticks '
        f'({nparts / 20:.1f}s). You are standing inside the footprint — use '
        f'spectator, or /function {ns}:go_above.","color":"gray"}}',
        # load before placing, so nothing lands in a chunk that is not ticking
        f"function {ns}:load",
        f"function {ns}:tick",
    ])

    fn("tick", [
        f"execute as @e[type=marker,tag={tag},limit=1] at @s "
        f"run function {ns}:dispatch",
        f"scoreboard players add #build {obj} 1",
        f"execute store result storage {ns}:ui part int 1 run "
        f"scoreboard players get #build {obj}",
        f"function {ns}:progress with storage {ns}:ui",
        f"execute if score #build {obj} matches ..{nparts - 1} run "
        f"schedule function {ns}:tick 1t replace",
        f"execute if score #build {obj} matches {nparts}.. run "
        f"function {ns}:done",
    ])

    # the action bar rather than chat: 36 lines of "placing..." is not progress
    # reporting, it is a wall of text you scroll past to find the answer
    fn("progress", [
        f'$title @a actionbar {{"text":"{name}: placing part $(part) of '
        f'{nparts}","color":"gray"}}',
    ])

    # `execute if score` rather than a macro, so this runs on any 1.21+ build
    fn("dispatch",
       [f"execute if score #build {obj} matches {i} run function {ns}:{f}"
        for i, f in enumerate(files)])

    fn("done", [
        f"scoreboard players set #run {obj} 0",
        # the anchor stays: `clear`, `status` and the teleports all read it,
        # and it is the only record of where the machine actually went
        f'tellraw @a {{"text":"{name} placed: {count:,} blocks, {dx} x {dy} x '
        f'{dz}. The control wall is at the -X end. /function {ns}:help for '
        f'what to do next.","color":"green"}}',
        f'title @a actionbar {{"text":"{name}: placed","color":"green"}}',
    ])

    fn("busy", [
        f'tellraw @a {{"text":"{name}: a build or clear is already running. '
        f'Wait for it, or /function {ns}:abort.","color":"red"}}',
    ])

    fn("abort", [
        f"schedule clear {ns}:tick",
        f"schedule clear {ns}:clear_tick",
        f"scoreboard players set #run {obj} 0",
        f"kill @e[type=marker,tag={tag}_clear]",
        f'tellraw @a {{"text":"{name}: stopped. The machine is half-placed; '
        f'run build again or clear it.","color":"yellow"}}',
    ])

    # --- taking it away -----------------------------------------------------
    # `fill` caps at 32,768 blocks a command and one Y layer is dx by dz, so the
    # layer is cut into Z strips that fit and a marker climbs one layer a tick.
    zstep = max(1, 32768 // max(1, dx))
    strips = [f"fill ~ ~ ~{z} ~{dx - 1} ~ ~{min(dz - 1, z + zstep - 1)} "
              f"minecraft:air replace"
              for z in range(0, dz, zstep)]

    fn("clear", [
        f"# Removes the machine wherever it was built. Run it from anywhere.",
        f"scoreboard objectives add {obj} dummy",
        f"execute if score #run {obj} matches 1 run function {ns}:busy",
        f"execute if score #run {obj} matches 1 run return fail",
        f"execute unless data storage {store} x run function {ns}:no_origin",
        f"execute unless data storage {store} x run return fail",
        f"scoreboard players set #run {obj} 1",
        f"scoreboard players set #clear {obj} 0",
        # force-load first. This is the whole bug: the fills only ever touched
        # chunks the server already had loaded, so `clear` took away the part of
        # the machine near the player and left the rest standing.
        f"function {ns}:load",
        f"function {ns}:clear_at with storage {store}",
    ])

    fn("clear_at", [
        f"$execute positioned $(x) $(y) $(z) run function {ns}:clear_begin",
    ])

    fn("clear_begin", [
        f"data remove storage {ns}:ui part",
        f"kill @e[type=marker,tag={tag}_clear]",
        f'summon marker ~ ~ ~ {{Tags:["{tag}_clear"]}}',
        f'tellraw @a {{"text":"{name}: clearing {dy} layers '
        f'({dy / 20:.1f}s)...","color":"gray"}}',
        f"function {ns}:clear_tick",
    ])

    fn("clear_tick", [
        f"execute as @e[type=marker,tag={tag}_clear,limit=1] at @s "
        f"run function {ns}:clear_slice",
        f"execute as @e[type=marker,tag={tag}_clear,limit=1] at @s "
        f"run tp @s ~ ~1 ~",
        f"scoreboard players add #clear {obj} 1",
        f"execute store result storage {ns}:ui layer int 1 run "
        f"scoreboard players get #clear {obj}",
        f"function {ns}:clear_progress with storage {ns}:ui",
        f"execute if score #clear {obj} matches ..{dy - 1} run "
        f"schedule function {ns}:clear_tick 1t replace",
        f"execute if score #clear {obj} matches {dy}.. run "
        f"function {ns}:clear_done",
    ])

    fn("clear_progress", [
        f'$title @a actionbar {{"text":"{name}: clearing layer $(layer) of '
        f'{dy}","color":"gray"}}',
    ])

    fn("clear_slice", strips)

    fn("clear_done", [
        f"kill @e[type=marker,tag={tag}_clear]",
        f"kill @e[type=marker,tag={tag}]",
        f"function {ns}:unload",
        f"data remove storage {store} x",
        f"data remove storage {store} y",
        f"data remove storage {store} z",
        f"scoreboard players set #run {obj} 0",
        f'tellraw @a {{"text":"{name} removed: {dy} layers, chunks released.",'
        f'"color":"gray"}}',
        f'title @a actionbar {{"text":"{name}: removed","color":"gray"}}',
    ])

    # --- keeping it loaded --------------------------------------------------
    # The single most important thing about running a machine this size in a
    # real world: **redstone only ticks in chunks the game is simulating.** This
    # build is dx x dz blocks; at Java's default simulation distance a player
    # sees a 21 x 21 square of chunks, so somebody at the control wall would
    # throw a lever and the far end would simply be frozen. No error, no answer.
    #
    # `/forceload` holds chunks at the ticket level that grants block ticking.
    # One command covers at most 256 chunks, so the footprint is tiled.
    tiles = []
    for x in range(0, dx, 256):
        for z in range(0, dz, 256):
            tiles.append(f"forceload add ~{x} ~{z} "
                         f"~{min(dx - 1, x + 255)} ~{min(dz - 1, z + 255)}")
    chunks = (-(-dx // 16)) * (-(-dz // 16))

    fn("load", [
        f"execute unless data storage {store} x run function {ns}:no_origin",
        f"execute unless data storage {store} x run return fail",
        f"function {ns}:load_at with storage {store}",
    ])
    fn("load_at",
       [f"$execute positioned $(x) $(y) $(z) run function {ns}:load_tiles"])
    fn("load_tiles", [
        f"# {-(-dx // 16)} x {-(-dz // 16)} = {chunks} chunks, "
        f"{len(tiles)} commands (256 chunks each is the cap).",
    ] + tiles + [
        f'tellraw @a {{"text":"{name}: {chunks} chunks force-loaded — the '
        f'machine ticks even where nobody is standing.","color":"green"}}',
    ])

    fn("unload", [
        f"execute unless data storage {store} x run function {ns}:no_origin",
        f"execute unless data storage {store} x run return fail",
        f"function {ns}:unload_at with storage {store}",
    ])
    fn("unload_at",
       [f"$execute positioned $(x) $(y) $(z) run function {ns}:unload_tiles"])
    fn("unload_tiles",
       [t.replace("forceload add", "forceload remove") for t in tiles] +
       [f'tellraw @a {{"text":"{name}: chunks released. The machine stops '
        f'ticking away from you.","color":"gray"}}'])

    fn("no_origin", [
        f'tellraw @a {{"text":"{name}: nothing built yet — no origin on '
        f'record. Stand at the -X -Y -Z corner and run /function '
        f'{ns}:build.","color":"red"}}',
    ])

    # --- saying where things are -------------------------------------------
    fn("status", [
        f"scoreboard objectives add {obj} dummy",
        f"execute unless data storage {store} x run function {ns}:no_origin",
        f"execute if data storage {store} x run "
        f"function {ns}:status_show with storage {store}",
        f"execute if score #run {obj} matches 1 if data storage {ns}:ui part "
        f"run function {ns}:status_running with storage {ns}:ui",
        f"execute if score #run {obj} matches 1 if data storage {ns}:ui layer "
        f"run function {ns}:status_clearing with storage {ns}:ui",
        f"execute if data storage {store} x unless entity "
        f"@e[type=marker,tag={tag}] run function {ns}:status_lost",
    ])
    fn("status_show", [
        f'$tellraw @a {{"text":"{name}: built at $(x) $(y) $(z), {dx} x {dy} x '
        f'{dz}, {count:,} blocks in {chunks} force-loaded chunks.",'
        f'"color":"green"}}',
    ])
    fn("status_running", [
        f'$tellraw @a {{"text":"{name}: working — part $(part) of {nparts}.",'
        f'"color":"yellow"}}',
    ])
    fn("status_clearing", [
        f'$tellraw @a {{"text":"{name}: clearing — layer $(layer) of {dy}.",'
        f'"color":"yellow"}}',
    ])
    fn("status_lost", [
        f'tellraw @a {{"text":"{name}: the origin marker is missing (chunks '
        f'unloaded, or it was killed). Coordinates above still work; run '
        f'/function {ns}:load to bring the chunks back.","color":"yellow"}}',
    ])

    fn("help", [
        f'tellraw @a {{"text":"{name} — {dx} x {dy} x {dz}, {count:,} blocks",'
        f'"color":"white"}}',
    ] + [
        f'tellraw @a {{"text":"  /function {ns}:{c}  — {d}","color":"gray"}}'
        for c, d in [
            ("build", "place it, from the -X -Y -Z corner"),
            ("clear", "take it away again, from anywhere"),
            ("status", "where it is and what it is doing"),
            ("load", "force-load its chunks so it ticks"),
            ("unload", "release them again"),
            ("abort", "stop a build or clear part way"),
        ] + [(f"go_{k}", f"teleport to the {k}") for k in sorted(marks)]
    ])

    # Teleports, because the control wall is one corner of a 555 x 973 machine
    # and walking to it is a minute of holding W in a trench.
    for key, (mx, my, mz) in sorted(marks.items()):
        fn(f"go_{key}", [
            f"execute unless data storage {store} x run function {ns}:no_origin",
            f"execute unless data storage {store} x run return fail",
            f"function {ns}:go_{key}_at with storage {store}",
        ])
        fn(f"go_{key}_at", [
            f"$execute positioned $(x) $(y) $(z) run "
            f"tp @s ~{mx} ~{my} ~{mz}",
            f'tellraw @s {{"text":"{name}: {key}","color":"gray"}}',
        ])

    for fname, lines in out.items():
        _write(os.path.join(fdir, f"{fname}.mcfunction"), lines)

    return {"entry": f"function {ns}:build", "clear": f"function {ns}:clear",
            "load": f"function {ns}:load", "unload": f"function {ns}:unload",
            "status": f"function {ns}:status", "help": f"function {ns}:help",
            "control_functions": sorted(out),
            "control_files": {f"{k}.mcfunction": "\n".join(v) + "\n"
                              for k, v in out.items()},
            "ticks": nparts, "clear_ticks": dy,
            "fills_per_layer": len(strips), "forceload_commands": len(tiles),
            "landmarks": marks,
            "chunks": chunks}
