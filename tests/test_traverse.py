"""The traversal pack, and the linter that is standing in for Minecraft.

There is no game in this test rig, so these commands are the one artefact in the
repository that nothing executes before a player does. Two rules follow.

**Anything checkable statically gets checked**: every function reference, every
objective, every block tag, every macro argument, every `minecraft:tick` hook.
`rscalc/packlint.py` does that, and the first test here proves the linter can
actually fail, because a checker that passes everything is worse than none.

**Anything not checkable gets said out loud rather than implied.** The feel of
the grappling hook is not tested. Whether 0.85 blocks a tick is pleasant, or
whether the dash overshoots, is not knowable from here.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc import mcbuild, packlint, traverse


NS = mcbuild.NAMESPACE


def _built():
    """The gadgets, alone in a pack, so the linter has something complete."""
    d = tempfile.mkdtemp()
    info = traverse.build(d, ns=NS, machine_help=[("build", "place it")])
    traverse.write_hooks(d, ns=NS)
    with open(os.path.join(d, "pack.mcmeta"), "w") as f:
        json.dump(mcbuild.pack_meta("traversal only, for the tests"), f)
    return d, info


def test_the_pack_lints_clean():
    d, info = _built()
    try:
        bad = packlint.lint(d)
        assert not bad, "\n  ".join([""] + bad)
        print(f"  {len(info['functions'])} gadget functions, "
              f"no unresolved reference: OK")
    finally:
        shutil.rmtree(d)


def test_the_linter_can_fail():
    """Eight ways to break a pack, each of which must be caught.

    This is the one that matters. The linter is the only thing between a typo
    and a player finding it, and every check in it was written by the same
    person who wrote the code it checks — so it gets to prove itself against
    damage rather than be trusted.
    """
    d, _ = _built()
    fdir = os.path.join(d, "data", NS, "function")

    def broken(what, path, find, put):
        copy = tempfile.mkdtemp()
        dst = os.path.join(copy, "p")
        shutil.copytree(d, dst)
        full = os.path.join(dst, path)
        body = open(full).read()
        assert find in body, f"{what}: {find!r} not in {path}"
        open(full, "w").write(body.replace(find, put, 1))
        found = packlint.lint(dst)
        shutil.rmtree(copy)
        assert found, f"{what}: the linter did not notice"
        return found[0]

    ns = NS
    fn = f"data/{ns}/function"
    cases = [
        ("a call to a function that is gone", f"{fn}/{traverse.PREFIX}/gear.mcfunction",
         f"{ns}:{traverse.PREFIX}/on", f"{ns}:{traverse.PREFIX}/onn"),
        ("a call from the tick loop that is gone", f"{fn}/{traverse.PREFIX}/tick.mcfunction",
         f"{ns}:{traverse.PREFIX}/grapple", f"{ns}:{traverse.PREFIX}/grappel"),
        ("an objective nothing creates", f"{fn}/{traverse.PREFIX}/tick.mcfunction",
         f"{ns}_dash=1..", "nope_dash=1.."),
        ("a tag nothing applies", f"{fn}/{traverse.PREFIX}/tick.mcfunction",
         f"tag={ns}_on", "tag=nope_on"),
        ("a block tag the pack does not ship",
         f"{fn}/{traverse.PREFIX}/grapple.mcfunction",
         f"#{ns}:passable", f"#{ns}:walkable"),
        ("a macro placeholder nothing supplies", f"{fn}/{traverse.PREFIX}/recall_at.mcfunction",
         "$(x)", "$(ex)"),
        ("an unbalanced component", f"data/{ns}/function/help.mcfunction",
         '"color":"white"}', '"color":"white"'),
        ("a misspelled command", f"{fn}/{traverse.PREFIX}/mark.mcfunction",
         "tellraw @s", "telraw @s"),
        # the one that actually shipped: an enum value from the wrong
        # vocabulary. It does not misbehave at run time — the function does not
        # load, and the command a player types is "Unknown function".
        ("a sound source that is not a sound source",
         f"{fn}/{traverse.PREFIX}/mount.mcfunction",
         "player @s", "players @s"),
        ("a tick hook pointing nowhere",
         "data/minecraft/tags/function/tick.json",
         f"{ns}:{traverse.PREFIX}/tick", f"{ns}:{traverse.PREFIX}/tock"),
    ]
    try:
        for what, path, find, put in cases:
            first = broken(what, path, find, put)
            print(f"    {what:38s} -> {first[:64]}")
        assert not packlint.lint(d), "and the unbroken pack still passes"
        print(f"  {len(cases)} deliberate breaks, every one caught, and the "
              f"clean pack still passes: OK")
    finally:
        shutil.rmtree(d)


def test_the_hook_never_moves_you_into_a_block():
    """Every teleport in the pack is gated on the destination being passable.

    A grappling hook that pulls you inside terrain is not a rough edge, it is
    suffocation. The rule is mechanical and worth stating mechanically: no `tp`
    in this pack may run unguarded, except the waypoint recall, which goes to a
    place the player was standing in.
    """
    d, _ = _built()
    try:
        fdir = os.path.join(d, "data", NS, "function", traverse.PREFIX)
        unguarded = []
        for name in sorted(os.listdir(fdir)):
            for n, line in enumerate(open(os.path.join(fdir, name)), 1):
                s = line.strip().lstrip("$")
                if " tp @" not in f" {s}" and not s.startswith("tp @"):
                    continue
                if name == "recall_at.mcfunction":
                    continue
                if f"if block ~ ~ ~ #{NS}:passable" not in s:
                    unguarded.append(f"{name}:{n}: {s}")
        assert not unguarded, "\n  ".join([""] + unguarded)
        print(f"  every teleport but the waypoint is gated on a passable "
              f"destination: OK")
    finally:
        shutil.rmtree(d)


def test_the_pull_moves_you_rather_than_teleporting_you():
    """The report from a real world was exact: *it just teleports the player*.

    It did. `tp @s` once a tick is twenty position corrections a second for the
    client to swallow — no interpolation, no momentum, and letting go leaves you
    hanging, because a teleport has no velocity to inherit.

    You cannot set a player's velocity from a command. You can set an entity's,
    and a rider moves with its vehicle, so the hook mounts you on an invisible
    marker armor stand and steers that instead. This pins the mechanism: the
    grapple path must contain no teleport at all, and must set Motion on
    whatever the player is riding.
    """
    d, _ = _built()
    try:
        fdir = os.path.join(d, "data", NS, "function", traverse.PREFIX)
        pull = ""
        for name in ("grapple", "thrust", "motion", "mount"):
            with open(os.path.join(fdir, f"{name}.mcfunction")) as f:
                pull += f.read()
        assert " tp @" not in pull and not pull.startswith("tp @"), \
            "the pull teleports again — that is the bug this replaced"
        assert "ride @s mount" in pull, "nothing puts the player on a vehicle"
        assert "on vehicle run data merge entity @s {Motion:[" in pull, \
            "nothing gives the vehicle a velocity"
        # and the vehicle has to be cleaned up, or you are stuck on it forever
        for name in ("release", "arrive", "unstick"):
            with open(os.path.join(fdir, f"{name}.mcfunction")) as f:
                body = f.read()
            assert "ride @s dismount" in body, name
            assert f"kill @e[type=armor_stand,tag={NS}_ride" in body, name
        print("  the pull sets Motion on a ridden entity and never teleports, "
              "and every exit dismounts and cleans up: OK")
    finally:
        shutil.rmtree(d)


def test_the_passable_tag_cannot_be_broken_by_one_bad_id():
    """A block tag containing an id the version lacks fails to load entirely.

    And a tag that failed to load looks exactly like a grappling hook that does
    nothing: `if block ~ ~ ~ #ns:passable` is simply never true. Only the four
    ids that have existed forever are required; everything else is optional, so
    a rename in some future version costs a blade of grass, not the hook.
    """
    d, _ = _built()
    try:
        tag = json.load(open(os.path.join(
            d, "data", NS, "tags", "block", "passable.json")))
        required = [v for v in tag["values"] if isinstance(v, str)]
        optional = [v for v in tag["values"] if isinstance(v, dict)]
        assert set(required) == {"minecraft:air", "minecraft:cave_air",
                                 "minecraft:void_air", "minecraft:water"}, required
        assert optional and all(v["required"] is False for v in optional)
        print(f"  {len(required)} required ids, {len(optional)} marked "
              f"optional so one rename cannot disable the hook: OK")
    finally:
        shutil.rmtree(d)


def test_the_gadgets_live_in_the_machine_s_namespace():
    """One pack, one namespace, one prefix to remember.

    The gadgets used to be a second download in a second namespace: two things
    to install, two `/reload`s to get wrong, and two prefixes to recall. They
    are `rscalc:move/...` now, beside `rscalc:build`, so tab completion after
    `/function rscalc:` lists everything this project can do.
    """
    d, info = _built()
    try:
        fdir = os.path.join(d, "data", NS, "function")
        assert os.path.isdir(os.path.join(fdir, traverse.PREFIX))
        assert all(f.startswith(traverse.PREFIX + "/") or f == "help"
                   for f in info["functions"]), info["functions"]
        hooks = os.path.join(d, "data", "minecraft", "tags", "function")
        tick = json.load(open(os.path.join(hooks, "tick.json")))["values"]
        assert tick == [f"{NS}:{traverse.PREFIX}/tick"], tick
        # the machine's world-start hook goes ahead of the gadgets', so the
        # chunks are back under force-load before anything looks at them
        load = json.load(open(os.path.join(hooks, "load.json")))["values"]
        assert load == [f"{NS}:{traverse.PREFIX}/load"], load
        traverse.write_hooks(d, ns=NS, on_load=[f"{NS}:sys/keep"])
        load = json.load(open(os.path.join(hooks, "load.json")))["values"]
        assert load == [f"{NS}:sys/keep", f"{NS}:{traverse.PREFIX}/load"], load
        print(f"  {len(info['functions'])} gadget functions under "
              f"{NS}:{traverse.PREFIX}/, in the machine's own namespace: OK")
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} traversal tests\n")
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
