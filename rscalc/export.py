"""Export a compiled World into a compact blob the browser demo can load.

Blocks are sorted by (y, z, x) and stored as a structure of arrays, which makes
each array highly repetitive and lets gzip do most of the work. The browser
inflates it with the built-in DecompressionStream, so no external library is
needed and the page stays self-contained.
"""

from __future__ import annotations

import base64
import gzip
import json
import struct

from .engine import World

KINDS = ["solid", "redstone_wire", "redstone_torch", "repeater",
         "comparator", "lever", "lamp", "redstone_block", "glass"]
KIND_ID = {k: i for i, k in enumerate(KINDS)}
DIRS6 = ["north", "south", "west", "east", "up", "down"]
DIR_ID = {d: i for i, d in enumerate(DIRS6)}


def encode_world(world: World):
    (x0, y0, z0), (x1, y1, z1) = world.bounds()
    items = sorted(world.blocks.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0]))
    n = len(items)
    xs = bytearray(); ys = bytearray(); zs = bytearray()
    kinds = bytearray(); metas = bytearray()
    for (x, y, z), b in items:
        xs += struct.pack("<H", x - x0)
        ys += struct.pack("<H", y - y0)
        zs += struct.pack("<H", z - z0)
        kinds.append(KIND_ID[b.kind])
        meta = 0
        if b.kind == "repeater":
            meta = DIR_ID[b.facing] | ((b.delay - 1) << 3)
        elif b.kind == "comparator":
            meta = DIR_ID[b.facing] | ((1 if b.mode == "subtract" else 0) << 3)
        elif b.kind == "redstone_torch":
            meta = DIR_ID[b.attach]
        elif b.kind == "lever":
            meta = DIR_ID[b.attach] | ((1 if b.on else 0) << 3)
        metas.append(meta)
    raw = bytes(xs) + bytes(ys) + bytes(zs) + bytes(kinds) + bytes(metas)
    blob = base64.b64encode(gzip.compress(raw, 9)).decode()
    return {
        "n": n,
        "origin": [x0, y0, z0],
        "dims": [x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1],
        "data": blob,
    }


def export_circuit(name, title, world, layout, meta=None):
    enc = encode_world(world)
    x0, y0, z0 = enc["origin"]

    def rel(p):
        return [p[0] - x0, p[1] - y0, p[2] - z0]

    enc.update({
        "name": name,
        "title": title,
        "levers": {k: rel(v) for k, v in layout.levers.items()},
        "outputs": {k: rel(v) for k, v in layout.outputs.items()},
        "stats": dict(layout.stats),
    })
    if meta:
        enc.update(meta)
    return enc


def write_bundle(path, circuits):
    payload = json.dumps({"circuits": circuits}, separators=(",", ":"))
    with open(path, "w") as f:
        f.write(payload)
    return len(payload)
