"""Cache a world's settled state, because relaxing one takes minutes.

`Engine.initialize_steady` walks the whole build to a fixed point so timing
starts from rest rather than from the power-on transient. On the Mk III machine
that is several minutes of work whose answer never changes, and it is needed by
the tests, the exporter and every experiment.

So it is computed once and written to disk, keyed by a digest of the actual
placed blocks. A cache that does not match the world it is asked about is
ignored rather than trusted — a stale steady state would corrupt every result
downstream while looking perfectly healthy.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import struct

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "out", "steady")


def world_digest(world):
    """A digest of the world's *structure* — what is placed, and how it faces.

    Deliberately not a digest of its state. Lever positions change as soon as
    anyone operates the machine, and keying the cache on them would mean the
    resting state stopped being findable the moment it was used. The saved state
    carries the lever positions along with everything else, so restoring it puts
    the levers back where they were when it was computed.
    """
    h = hashlib.sha256()
    for pos in sorted(world.blocks):
        b = world.blocks[pos]
        h.update(struct.pack("<iii", *pos))
        h.update(b.kind.encode())
        h.update(str(getattr(b, "facing", "")).encode())
        h.update(str(getattr(b, "attach", "")).encode())
        h.update(str(getattr(b, "delay", "")).encode())
        h.update(str(getattr(b, "mode", "")).encode())
    return h.hexdigest()[:32]


def _path(digest):
    return os.path.join(CACHE_DIR, f"{digest}.bin.gz")


#: every mutable field a block carries. A torch's state lives in `lit`, a
#: repeater's in `powered`, a comparator's in `out` — save only some of them and
#: the restored world looks plausible and computes nonsense.
def _pack(b):
    flags = ((1 if b.lit else 0) | (2 if b.powered else 0)
             | (4 if b.on else 0) | (8 if b.locked else 0))
    return (min(15, int(b.power or 0)) | (min(15, int(b.out or 0)) << 4), flags)


def _unpack(b, a, flags):
    b.power = a & 15
    b.out = (a >> 4) & 15
    b.lit = bool(flags & 1)
    b.powered = bool(flags & 2)
    b.on = bool(flags & 4)
    b.locked = bool(flags & 8)


def save(world, digest=None):
    digest = digest or world_digest(world)
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = bytearray()
    for pos in sorted(world.blocks):
        a, f = _pack(world.blocks[pos])
        out.append(a)
        out.append(f)
    with gzip.open(_path(digest), "wb") as f:
        f.write(bytes(out))
    return _path(digest)


def load(world, digest=None):
    """Restore a cached state onto `world`. Returns True if one was found."""
    digest = digest or world_digest(world)
    p = _path(digest)
    if not os.path.exists(p):
        return False
    with gzip.open(p, "rb") as f:
        data = f.read()
    keys = sorted(world.blocks)
    if len(data) != 2 * len(keys):
        return False
    for i, pos in enumerate(keys):
        _unpack(world.blocks[pos], data[2 * i], data[2 * i + 1])
    return True


def settled_engine(world, engine_cls, verify=True, use_cache=True,
                   quiet=False):
    """An Engine on `world`, at rest, using the cache when it can.

    With `verify`, one relaxation pass is run over the restored state and must
    change nothing — a cheap, complete check that the cache really is this
    world's fixed point rather than something that merely fits.
    """
    digest = world_digest(world)
    if use_cache and load(world, digest):
        # skip the power-on settle entirely: the state is already the answer
        e = engine_cls(world, settle_on_init=False)
        e.adopt_state(check=verify)
        if not quiet:
            print(f"  steady state: cache hit {digest}"
                  f"{' (verified)' if verify else ''}", flush=True)
        return e
    e = engine_cls(world)
    e.initialize_steady()
    if use_cache:
        save(world, digest)
        if not quiet:
            print(f"  steady state: computed and cached as {digest}",
                  flush=True)
    return e
