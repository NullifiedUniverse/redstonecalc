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


def _built(mc=mcbuild.MC_VERSION_DEFAULT):
    d = tempfile.mkdtemp()
    info = traverse.build(d, mc_version=mc)
    return d, info


def test_the_pack_lints_clean():
    d, info = _built()
    try:
        bad = packlint.lint(d)
        assert not bad, "\n  ".join([""] + bad)
        print(f"  {len(info['functions'])} functions, {info['files']} files, "
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
    fdir = os.path.join(d, "data", traverse.NS, "function")

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

    ns = traverse.NS
    fn = f"data/{ns}/function"
    cases = [
        ("a call to a function that is gone", f"{fn}/gear.mcfunction",
         f"{ns}:on", f"{ns}:onn"),
        ("a schedule for a missing function", f"{fn}/tick.mcfunction",
         f"function {ns}:grapple", f"function {ns}:grappel"),
        ("an objective nothing creates", f"{fn}/tick.mcfunction",
         f"{ns}_dash=1..", "nope_dash=1.."),
        ("a tag nothing applies", f"{fn}/tick.mcfunction",
         f"tag={ns}_on", "tag=nope_on"),
        ("a block tag the pack does not ship", f"{fn}/grapple.mcfunction",
         f"#{ns}:passable", f"#{ns}:walkable"),
        ("a macro placeholder nothing supplies", f"{fn}/recall_at.mcfunction",
         "$(x)", "$(ex)"),
        ("an unbalanced component", f"{fn}/help.mcfunction",
         '"color":"white"}', '"color":"white"'),
        ("a misspelled command", f"{fn}/mark.mcfunction",
         "tellraw @s", "telraw @s"),
        ("a tick hook pointing nowhere",
         "data/minecraft/tags/function/tick.json",
         f"{ns}:tick", f"{ns}:tock"),
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
        fdir = os.path.join(d, "data", traverse.NS, "function")
        unguarded = []
        for name in sorted(os.listdir(fdir)):
            for n, line in enumerate(open(os.path.join(fdir, name)), 1):
                s = line.strip().lstrip("$")
                if " tp @" not in f" {s}" and not s.startswith("tp @"):
                    continue
                if name == "recall_at.mcfunction":
                    continue
                if f"if block ~ ~ ~ #{traverse.NS}:passable" not in s:
                    unguarded.append(f"{name}:{n}: {s}")
        assert not unguarded, "\n  ".join([""] + unguarded)
        print(f"  every teleport but the waypoint is gated on a passable "
              f"destination: OK")
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
            d, "data", traverse.NS, "tags", "block", "passable.json")))
        required = [v for v in tag["values"] if isinstance(v, str)]
        optional = [v for v in tag["values"] if isinstance(v, dict)]
        assert set(required) == {"minecraft:air", "minecraft:cave_air",
                                 "minecraft:void_air", "minecraft:water"}, required
        assert optional and all(v["required"] is False for v in optional)
        print(f"  {len(required)} required ids, {len(optional)} marked "
              f"optional so one rename cannot disable the hook: OK")
    finally:
        shutil.rmtree(d)


def test_it_targets_the_version_it_says_it_does():
    d, _ = _built()
    try:
        meta = json.load(open(os.path.join(d, "pack.mcmeta")))["pack"]
        assert meta["max_format"] == list(mcbuild.MC_FORMATS["26.2"])
        shutil.rmtree(d)
        d, _ = _built("1.21")
        meta = json.load(open(os.path.join(d, "pack.mcmeta")))["pack"]
        assert meta == {"description": meta["description"], "pack_format": 48}
        print("  pack.mcmeta follows --mc, in both the new and the old "
              "spelling: OK")
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
