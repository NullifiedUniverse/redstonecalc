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


def encode_world(world: World, engine=None):
    """Serialise the blocks, and optionally the state they have settled into.

    Relaxing a half-million-block world to its steady state takes minutes; doing
    it again in the browser on every page load would be absurd when the answer
    is already known here. With `engine` given, each block's settled power and
    lit flag ride along and the page starts at rest.
    """
    (x0, y0, z0), (x1, y1, z1) = world.bounds()
    items = sorted(world.blocks.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0]))
    n = len(items)
    xs = bytearray(); ys = bytearray(); zs = bytearray()
    kinds = bytearray(); metas = bytearray()
    powers = bytearray(); lits = bytearray()
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
        if engine is not None:
            # The browser engine keeps a comparator's output in `power` and a
            # repeater's and lever's in `lit`, where the Python one uses `out`,
            # `powered` and `on`. Translate here rather than shipping fields the
            # page would silently ignore.
            if b.kind == "comparator":
                p = b.out
            elif b.kind in ("redstone_wire", "lamp"):
                p = b.power
            else:
                p = 0
            if b.kind == "redstone_torch":
                l = b.lit
            elif b.kind == "repeater":
                l = b.powered
            elif b.kind == "lever":
                l = b.on
            else:
                l = False
            powers.append(min(255, int(p or 0)))
            lits.append(1 if l else 0)
    raw = bytes(xs) + bytes(ys) + bytes(zs) + bytes(kinds) + bytes(metas)
    out = {
        "n": n,
        "origin": [x0, y0, z0],
        "dims": [x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1],
        "data": base64.b64encode(gzip.compress(raw, 9)).decode(),
    }
    if engine is not None:
        out["state"] = base64.b64encode(
            gzip.compress(bytes(powers) + bytes(lits), 9)).decode()
    return out


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
