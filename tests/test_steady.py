"""The steady-state cache, which every other measurement is taken on top of.

`rscalc/steady.py` exists because relaxing the Mk III to rest takes minutes,
and the answer never changes. So it is computed once and written to disk, and
from then on the tests, the exporter and every experiment start from a file.

That makes it the one module whose faults do not look like faults. A wrong
resting state is a *plausible* resting state: every torch is lit or unlit, every
repeater is powered or not, the machine boots and answers questions and gets
them wrong in a way that reads as a design bug three layers up. Nothing about a
restored world announces that it came from the wrong world, or that a field went
missing on the way through.

So the module's two claims get checked directly:

  * the saved state carries **every** mutable field a block has, and
  * the digest keys on **structure** and ignores **state**, so operating the
    machine does not invalidate its own cache.

Both are cheap to state and easy to break by accident — `_pack` packs six
fields into two bytes by hand, and `world_digest` hashes an explicit list of
attributes, so a seventh field or a new block property is silently left out
until someone checks. This is that check.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc import steady

#: what `_pack` claims to carry
STATE_FIELDS = ("power", "out", "lit", "powered", "on", "locked")


def floor(w, x0, x1, y, z0, z1):
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            w.solid((x, y, z))


def build():
    """A world small enough to settle instantly with every field in play.

    Deliberately not a corner of the real machine. This needs a lit torch *and*
    an unlit one, a locked repeater *and* a free one, dust at 15 *and* dust at
    0, a comparator whose output is neither — because a round trip that only
    ever moves one value through a field cannot tell that the field was
    dropped. Rows are spaced two apart in Z so that only the ones meant to
    interact do.
    """
    w = World()
    floor(w, -6, 12, -1, -6, 5)

    # z=0: a driven line, ending in a torch inverter and a lit lamp
    w.lever((-6, 0, 0), attach="down", on=True)
    for x in range(-5, 0):
        w.wire((x, 0, 0))
    w.repeater((0, 0, 0), facing="east", delay=2)
    w.solid((1, 0, 0))                     # strongly powered
    w.wire((2, 0, 0))                      # 15
    w.wire((3, 0, 0))                      # 14, and a pair points, a dot does
    w.solid((4, 0, 0))                     # not: so this is weakly powered
    w.torch((5, 0, 0), attach="west")      # and this torch goes out
    w.wire((6, 0, 0))                      # leaving this dark
    w.torch((8, 0, 0), attach="down")      # fed by nothing, so it stays lit
    w.wire((9, 0, 0))
    w.lamp((10, 0, 0))                     # lit

    # z=2: nothing driving it, so there is an off lever and a quiet repeater
    w.lever((-6, 0, 2), attach="down", on=False)
    w.wire((-5, 0, 2))
    w.repeater((-4, 0, 2), facing="east", delay=3)
    w.wire((-3, 0, 2))

    # z=4: a comparator far enough down a dust run that `out` is partial
    w.lever((-6, 0, 4), attach="down", on=True)
    for x in range(-5, 3):
        w.wire((x, 0, 4))
    w.comparator((3, 0, 4), facing="east", mode="subtract")
    w.wire((4, 0, 4))

    # z=-3: a repeater held from the side by another, so `locked` is set
    w.lever((-6, 0, -3), attach="down", on=True)
    for x in range(-5, 0):
        w.wire((x, 0, -3))
    w.repeater((0, 0, -3), facing="east", delay=1)     # the latch
    w.wire((1, 0, -3))
    w.repeater((0, 0, -4), facing="south", delay=1)    # locks it from the side
    w.lever((0, 0, -5), attach="down", on=True)        # holds the lock on
    return w


def settled():
    w = build()
    e = Engine(w)
    e.initialize_steady()
    return w, e


def snapshot(w):
    return {p: tuple(getattr(b, f) for f in STATE_FIELDS)
            for p, b in w.blocks.items()}


def scramble(w):
    """Put every mutable field somewhere it certainly does not belong, so a
    field the cache forgets to restore stays visibly wrong rather than
    accidentally matching what was already there."""
    for b in w.blocks.values():
        b.power = 7 if b.power != 7 else 3
        b.out = 5 if b.out != 5 else 2
        b.lit = not b.lit
        b.powered = not b.powered
        b.on = not b.on
        b.locked = not b.locked


def test_the_world_exercises_every_field():
    """The round trip below is only worth as much as the states it moves.

    A cache test on a world where `locked` is False everywhere and every dust
    reads 0 passes whether or not those fields are saved at all — restoring
    nothing and restoring correctly look identical. So each saved field gets
    its own claim about the world, and every one has to hold before the round
    trip is worth running.
    """
    w, _ = settled()
    B = list(w.blocks.values())
    of = lambda k: [b for b in B if b.kind == k]
    claims = {
        "power": (any(b.power == 15 for b in of("redstone_wire"))
                  and any(b.power == 0 for b in of("redstone_wire")),
                  "dust at both 15 and 0"),
        "out": (any(0 < b.out < 15 for b in of("comparator")),
                "a comparator whose output is neither 0 nor 15"),
        "lit": (any(b.lit for b in of("redstone_torch"))
                and any(not b.lit for b in of("redstone_torch")),
                "a lit torch and an unlit one"),
        "powered": (any(b.powered for b in of("repeater"))
                    and any(not b.powered for b in of("repeater")),
                    "a powered repeater and an idle one"),
        "on": (any(b.on for b in of("lever"))
               and any(not b.on for b in of("lever")),
               "a lever on and a lever off"),
        "locked": (any(b.locked for b in of("repeater"))
                   and any(not b.locked for b in of("repeater")),
                   "a locked repeater and a free one"),
    }
    assert set(claims) == set(STATE_FIELDS), \
        f"a saved field has no claim about it: {set(STATE_FIELDS) ^ set(claims)}"
    thin = [f"{f} (wanted {why})" for f, (ok, why) in claims.items() if not ok]
    assert not thin, ("the test world does not exercise " + "; ".join(thin)
                      + " — a cache that dropped those would still pass")
    print(f"  the test world exercises all {len(STATE_FIELDS)} saved fields "
          f"in both directions: OK")


def test_every_mutable_field_survives_the_round_trip():
    w, _ = settled()
    want = snapshot(w)
    digest = steady.world_digest(w)
    path = steady.save(w, digest)
    try:
        scramble(w)
        assert snapshot(w) != want, "the scramble did nothing"
        assert steady.load(w, digest), "the state just saved was not found"
        got = snapshot(w)
        wrong = {p: (want[p], got[p]) for p in want if want[p] != got[p]}
        assert not wrong, (
            f"{len(wrong)} of {len(want)} blocks came back changed, e.g. "
            f"{list(wrong.items())[:2]} as (saved, restored) over "
            f"{STATE_FIELDS}")
        print(f"  {len(want)} blocks x {len(STATE_FIELDS)} fields restored "
              f"exactly: OK")
    finally:
        os.remove(path)


def test_the_digest_ignores_state_and_follows_structure():
    """Operating the machine must not invalidate its own cache; rebuilding it
    must. The first half is why the digest exists at all — lever positions
    change the moment anyone uses the thing."""
    w, e = settled()
    before = steady.world_digest(w)

    e.set_lever((-6, 0, 0), False)
    e.run_until_stable(400)
    assert any(b.on is False for b in w.blocks.values())
    assert steady.world_digest(w) == before, \
        "throwing a lever changed the digest, so every use would miss"

    edits = {
        "a repeater's delay": lambda u: setattr(u.blocks[(0, 0, 0)],
                                                "delay", 4),
        "a repeater's facing": lambda u: setattr(u.blocks[(0, 0, 0)],
                                                 "facing", "west"),
        "a torch's support": lambda u: setattr(u.blocks[(5, 0, 0)],
                                               "attach", "east"),
        "a comparator's mode": lambda u: setattr(u.blocks[(3, 0, 4)],
                                                 "mode", "compare"),
        "a block's kind": lambda u: setattr(u.blocks[(2, 0, 0)],
                                            "kind", "lamp"),
        "one more block": lambda u: u.wire((12, 0, 0)),
        "one fewer block": lambda u: u.blocks.pop((9, 0, 0)),
        "the same blocks somewhere else":
            lambda u: (u.blocks.pop((9, 0, 0)), u.wire((12, 0, 0))),
    }
    missed = []
    for what, edit in edits.items():
        u = build()
        base = steady.world_digest(u)
        edit(u)
        if steady.world_digest(u) == base:
            missed.append(what)
    assert not missed, f"the digest does not notice {missed}"
    print(f"  the digest ignores state and notices all {len(edits)} "
          f"structural edits: OK")


def test_a_cache_that_does_not_fit_is_refused():
    """Two ways a cache can be wrong: it is another world's, or it is damaged.
    Both have to end in a recompute, not in a plausible-looking restore."""
    w, _ = settled()
    digest = steady.world_digest(w)
    path = steady.save(w, digest)
    try:
        other = build()
        other.wire((12, 0, 0))             # a different world, same shape
        assert steady.world_digest(other) != digest
        assert not steady.load(other), \
            "a world with no cache of its own accepted somebody else's"

        import gzip
        with gzip.open(path, "rb") as f:
            data = f.read()
        with gzip.open(path, "wb") as f:
            f.write(data[:-2])             # one block short
        assert not steady.load(w, digest), \
            "a truncated cache was restored anyway"
    finally:
        os.remove(path)
    print("  a foreign cache and a truncated one are both refused: OK")


def test_a_lying_cache_is_caught_when_it_is_adopted():
    """The last line of defence. A file that is the right length, for the right
    digest, and simply wrong — the case no checksum catches — is caught by
    running one relaxation pass over it and requiring nothing to move."""
    w, _ = settled()
    digest = steady.world_digest(w)
    path = steady.save(w, digest)
    try:
        lit = next(p for p, b in w.blocks.items()
                   if b.kind == "redstone_torch" and b.lit)
        w.blocks[lit].lit = False           # a resting state that does not rest
        steady.save(w, digest)

        fresh = build()
        try:
            steady.settled_engine(fresh, Engine, verify=True, quiet=True)
        except RuntimeError as ex:
            assert str(lit) in str(ex), \
                f"caught, but the complaint does not name {lit}: {ex}"
            print(f"  a wrong-but-well-formed cache is caught on adoption, "
                  f"naming the block that lied: OK")
            return
        raise AssertionError(
            "a cache with a torch flipped was adopted as the resting state")
    finally:
        os.remove(path)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} steady-state cache tests\n")
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
