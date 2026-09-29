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


def test_the_pack_does_not_quietly_raise_the_version_it_needs():
    """A datapack cannot say "I need at least X", so this measures it instead.

    The format number in `pack.mcmeta` is a compatibility *claim*. What decides
    whether the pack works is whether the game can parse its commands, and a
    command from the future is a parse error that takes its whole function down
    — "Unknown function" for a file that is plainly in the zip, which is the
    failure DESIGN §38 chased twice.

    The floor is *measured* against each version's own command grammar
    (`mccheck.floors`), and pinned so that raising it is a decision somebody
    makes on purpose. It was 1.20.5 for a while, entirely because three `give`
    lines carried decorative `custom_name` components the pack never read.
    """
    from rscalc import mccheck
    d, _ = _built()
    try:
        res = mccheck.floors(d)
        assert res["core"] == "1.20.3", (
            f"the gadgets now need {res['core']}: "
            f"{[p for p in res['per_version'][res['core']]][:2]} — every "
            f"player below that loses the whole function, not the feature")
        for line in open(os.path.join(d, "data", NS, "function",
                                      traverse.PREFIX, "gear.mcfunction")):
            if line.startswith("give "):
                assert "[" not in line, (
                    f"an item component on a give that nothing reads: {line!r}")
        print(f"  the movement half loads from Minecraft {res['core']} and "
              f"the gear is plain items: OK")
    finally:
        shutil.rmtree(d)


def test_the_hook_never_moves_you_into_a_block():
    """Every teleport in the pack is gated on the destination being passable.

    A grappling hook that pulls you inside terrain is not a rough edge, it is
    suffocation. The rule is mechanical and worth stating mechanically: no `tp`
    in this pack may run unguarded, except the waypoint recall, which goes to a
    place the player was standing in — and the pull, whose guard cannot sit on
    the same line as its `tp` because that runs *on the vehicle*, 0.6 blocks off
    the player, and so lives in `grapple` instead. That is checked separately
    and precisely: both the foot cell and the head cell one block along the
    line must be free, and the function must have returned before it pulls.
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
                if name in ("recall_at.mcfunction", "pull.mcfunction"):
                    continue
                if f"if block ~ ~ ~ #{NS}:passable" not in s:
                    unguarded.append(f"{name}:{n}: {s}")
        assert not unguarded, "\n  ".join([""] + unguarded)

        grapple = open(os.path.join(fdir, "grapple.mcfunction")).read().splitlines()
        pull_at = next(i for i, l in enumerate(grapple)
                       if l.startswith(f"function {NS}:{traverse.PREFIX}/pull"))
        guards = [i for i, l in enumerate(grapple)
                  if "#free" in l and "unless block" in l]
        foot = [l for l in grapple if "unless block ~ ~ ~ " in l and "#free" in l]
        head = [l for l in grapple if "unless block ~ ~1 ~ " in l and "#free" in l]
        assert foot and head, "the pull checks the foot cell or the head cell, not both"
        stop = next(i for i, l in enumerate(grapple)
                    if "#free" in l and "matches 0" in l and "return fail" in l)
        assert max(guards) < stop < pull_at, (
            "the wall guard has to run, and return, before anything is pulled")
        print("  every teleport but the waypoint is gated on a passable "
              "destination, and the pull is gated on foot AND head cells before "
              "it runs: OK")
    finally:
        shutil.rmtree(d)


def test_the_pull_moves_the_vehicle_by_tp_and_never_relies_on_motion():
    """The vehicle cannot be moved by `Motion`; the game's own code says so.

    ArmorStand, Minecraft 26.2, from `javap -c`:

        private boolean hasPhysics() { return !isMarker() && !isNoGravity(); }
        public  void    travel(Vec3 v) { if (!hasPhysics()) return; super.travel(v); }

    The second design summoned its mount with `Marker:1b` and `NoGravity:1b` and
    then wrote `Motion` onto it. For that stand `travel` returns before doing
    anything, so `Motion` is written and never read: the player was mounted on
    something that ignored every instruction, and the rewrite meant to fix a
    hook that "just teleports the player" produced one that did not move them.

    So nothing in the gadgets may write `Motion`; the vehicle is moved by `tp`,
    on the vehicle, which is entity movement (interpolated by the client) and
    not player movement (twenty corrections a second); and the stand is
    summoned 0.6 up, because a player's vehicle attachment is (0, 0.6, 0) and
    a Marker stand is zero tall, so an un-offset rider sits 0.6 below it.
    """
    d, _ = _built()
    try:
        fdir = os.path.join(d, "data", NS, "function", traverse.PREFIX)
        text = {n: open(os.path.join(fdir, n)).read()
                for n in sorted(os.listdir(fdir))}

        for name, body in text.items():
            assert "Motion" not in body, (
                f"{name} writes Motion. A Marker or NoGravity armour stand "
                f"never reads it (ArmorStand.hasPhysics), so it moves nothing")

        pull = text["pull.mcfunction"]
        assert pull.startswith("$execute on vehicle at @s facing entity"), pull
        assert " run tp @s ^ ^ ^$(d)" in pull, pull
        assert "on vehicle" in pull and "tp @s" in pull

        # the player is never the thing being teleported by the pull
        for name in ("grapple.mcfunction", "pull.mcfunction", "mount.mcfunction",
                     "release.mcfunction", "arrive.mcfunction"):
            for line in text[name].splitlines():
                assert " tp @s" not in f" {line}" or "on vehicle" in line, \
                    f"{name}: teleports the player: {line}"

        mount = text["mount.mcfunction"]
        assert "summon minecraft:armor_stand ~ ~0.6 ~" in mount, (
            "the stand is not lifted by the rider's vehicle attachment (0.6)")
        assert "Marker:1b" in mount and "NoGravity:1b" in mount
        assert "ride @s mount @e[type=armor_stand," in mount
        assert "limit=1" in mount.split("ride @s mount")[1].split("\n")[0]

        # every way out is by killing OUR stand: dismounting is a consequence
        for name in ("release.mcfunction", "arrive.mcfunction"):
            body = text[name]
            assert "ride @s dismount" not in body, (
                f"{name} dismounts unconditionally — that throws you off every "
                f"horse and boat, every tick")
        assert (f"execute on vehicle if entity @s[tag={NS}_ride] run kill @s"
                in text["release.mcfunction"])
        assert "ride @s dismount" in text["unstick.mcfunction"]
        print("  the pull is one `tp` on the vehicle, nothing writes Motion, the "
              "stand is lifted 0.6, and only our own stand is ever removed: OK")
    finally:
        shutil.rmtree(d)


def test_the_hook_only_lets_go_of_its_own_mount():
    """`grapple` runs every tick for every player carrying the gear.

    Its first two lines used to be `release` — which ran `ride @s dismount` —
    for anyone with no bobber out, which is nearly everyone nearly always. Put
    the gadgets on and you were thrown off every horse, boat and minecart the
    instant you mounted it.

    Release is now gated on a tag that only `mount` sets, and what it removes is
    the mount found *under you* carrying this pack's tag.
    """
    d, _ = _built()
    try:
        fdir = os.path.join(d, "data", NS, "function", traverse.PREFIX)
        grapple = open(os.path.join(fdir, "grapple.mcfunction")).read().splitlines()
        first = grapple[0]
        assert "function" in first and "/release" in first
        assert f"if entity @s[tag={NS}_hooked]" in first, (
            "release runs for everyone, not just for someone actually hooked")
        mount = open(os.path.join(fdir, "mount.mcfunction")).read()
        assert f"tag @s add {NS}_hooked" in mount
        release = open(os.path.join(fdir, "release.mcfunction")).read()
        assert f"tag @s remove {NS}_hooked" in release
        # a hook that never landed must not start pulling: it would drag you
        # after a projectile that is still in the air
        landed = [l for l in grapple if "OnGround:1b" in l]
        assert landed and "return fail" in landed[0], grapple
        # and a stand nobody is sitting on is cleaned up
        assert "on passengers" in open(os.path.join(fdir, "sweep.mcfunction")).read()

        # The lifecycle, which nothing could have shown while the pull did not
        # move: a cast that has done its job stays done until it is reeled in.
        # Without that, standing beside the bobber you arrived at fires the
        # arrival sound and particles every tick, and walking away yanks you
        # straight back.
        for line in grapple:
            if "function" in line and "/arrive" in line:
                assert f"if entity @s[tag={NS}_hooked]" in line, (
                    f"arrive fires for someone who is not hooked, so it repeats "
                    f"every tick while they stand near the bobber: {line}")
        spent_idx = next(i for i, l in enumerate(grapple)
                         if f"if entity @s[tag={NS}_spent] run return fail" in l)
        land_idx = next(i for i, l in enumerate(grapple) if "OnGround:1b" in l)
        assert spent_idx < land_idx, "a spent cast is checked after the landing test"
        assert any(f"unless entity" in l and f"remove {NS}_spent" in l
                   for l in grapple), "reeling in never clears the spent mark"
        assert f"tag @s add {NS}_spent" in release, (
            "letting go does not mark the cast as finished, so it re-hooks")
        # turning the gadgets off lets go first: once the `on` tag is gone
        # `grapple` never runs again, and a hook left mid-pull is a player riding
        # a stand nothing steers
        off = open(os.path.join(fdir, "off.mcfunction")).read().splitlines()
        assert off[0] == f"function {NS}:{traverse.PREFIX}/release", off
        assert f"tag @s remove {NS}_on" in off
        print("  release only happens to a hooked player, only removes their own "
              "mount, and waits for the bobber to land: OK")
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
