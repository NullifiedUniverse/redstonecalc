"""The pet head, read back the way the game reads it.

Nothing here runs Minecraft, so every claim this file makes is about the
*commands* — which is exactly the class of bug that has actually bitten this
project: a selector that matches nothing, a count that is not a count, an
ownership test that resolves to somebody else's pet. Each test below is one of
those, written after finding it.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc import mcbuild, packlint, pet, traverse

NS = mcbuild.NAMESPACE


def _built():
    """The pet beside the gadgets, since the pet borrows their passable tag."""
    d = tempfile.mkdtemp()
    info = pet.build(d, ns=NS)
    traverse.build(d, ns=NS)
    traverse.write_hooks(d, ns=NS, on_tick=[f"{NS}:{pet.PREFIX}/tick"],
                         on_load=[f"{NS}:{pet.PREFIX}/load"])
    with open(os.path.join(d, "pack.mcmeta"), "w") as f:
        json.dump(mcbuild.pack_meta("pet test"), f)
    return d, info


def _text(d, name):
    with open(os.path.join(d, "data", NS, "function",
                           f"{pet.PREFIX}/{name}.mcfunction")) as f:
        return f.read()


def test_the_pack_lints_clean():
    d, info = _built()
    try:
        problems = packlint.lint(d)
        assert not problems, problems[:5]
        print(f"  {len(info['functions'])} pet functions, no unresolved "
              f"reference: OK")
    finally:
        shutil.rmtree(d)


def test_the_pet_is_never_somebody_elses():
    """`#owner` is a global, and a player with no pet used to leave it stale.

    `dismiss` calls `select` and then kills everything tagged mine. If a player
    with no pet runs it, `scoreboard players operation #owner = @s` fails
    silently, `#owner` keeps the previous player's id, and the kill takes *their*
    pet. The fix is to clear it to a value no pet can hold before reading.
    """
    d, _ = _built()
    try:
        sel = _text(d, "select").splitlines()
        assert sel[0].startswith(f"scoreboard players set #owner"), sel[0]
        assert sel[0].endswith(" 0"), sel[0]
        assert "matches 1.." in sel[1], sel[1]
        # ...and no pet may hold 0: ids come from a counter that is incremented
        # before it is read
        get = _text(d, "get")
        assert f"scoreboard players add #next {NS}_pet 1" in get
        i_add = get.index("#next")
        i_use = get.index(f"operation @s {NS}_pet = #next")
        assert i_add < i_use, "the id is read before it is incremented"
        print("  ownership clears to 0 before it is read, and no pet can be "
              "0: OK")
    finally:
        shutil.rmtree(d)


def test_the_trail_is_capped_by_a_count_and_not_by_a_limit():
    """`if entity @e[...,limit=65]` is true when *one* entity matches.

    Written that way the trail was trimmed on every single crumb, so the pet
    never had more than one waypoint and walked straight at the player — which
    is the beeline the whole path-tracing idea exists to avoid. `store result
    ... if entity` is the count.
    """
    d, _ = _built()
    try:
        crumb = _text(d, "crumb")
        assert "execute store result score #crumbs" in crumb, crumb
        assert "if entity @e[type=marker" in crumb
        assert f"matches {pet.TRAIL + 1}.." in crumb
        for line in crumb.splitlines():
            assert not (line.startswith("execute if entity") and
                        "limit=" in line), \
                f"a selector limit used as a count: {line}"
        print(f"  the trail is capped at {pet.TRAIL} by counting, not by a "
              f"selector limit: OK")
    finally:
        shutil.rmtree(d)


def test_the_pet_keeps_fighting_when_it_has_caught_up():
    """The moment it is standing beside you is when it most needs to bite.

    An early `return fail` for "no trail left" sat in front of the fight, so a
    pet that had caught up was inert — which is precisely the state it is in
    whenever you stop moving, and whenever something walks up to you.
    """
    d, _ = _built()
    try:
        drive = _text(d, "drive").splitlines()
        fight = [i for i, l in enumerate(drive)
                 if l.endswith(f"function {NS}:{pet.PREFIX}/fight")]
        assert fight, "the pet never fights"
        before = drive[:fight[0]]
        assert not any("return fail" in l for l in before), \
            f"an early return sits in front of the fight: {before}"
        print("  the fight runs whether or not there is trail left: OK")
    finally:
        shutil.rmtree(d)


def test_the_pet_finds_its_owner_by_a_tag_a_player_actually_carries():
    """`mine` goes on the pet and the crumbs. It never goes on a player.

    Testing `@a[tag=mine]` for "is my owner nearby" therefore matched nothing,
    and the give-up-and-teleport branch fired every tick — a pet that never
    walked anywhere because it was always being recalled.
    """
    d, _ = _built()
    try:
        sel, drive = _text(d, "select"), _text(d, "drive")
        assert f"tag @s add {NS}_boss" in sel
        assert f"tag @a remove {NS}_boss" in sel, \
            "the owner tag is never cleared, so it accumulates across players"
        assert f"@a[tag={NS}_boss" in drive, drive
        for line in drive.splitlines():
            assert f"@a[tag={NS}_mine" not in line, \
                f"the pet looks for its owner by a tag no player has: {line}"
        print("  the owner is found by a tag the owner actually carries: OK")
    finally:
        shutil.rmtree(d)


def test_the_head_goes_on_with_a_command_not_with_nbt():
    """Entity equipment moved from `ArmorItems` to `equipment` in 1.21.5.

    A `summon` carrying either spelling works on one side of that line and
    silently produces a headless invisible armour stand on the other — a pet you
    cannot see at all, with nothing in the log. `/item replace entity` has meant
    the same thing throughout.
    """
    d, _ = _built()
    try:
        get, head = _text(d, "get"), _text(d, "head")
        assert "ArmorItems" not in get and "equipment" not in get, get
        assert head.startswith("$item replace entity "), head
        assert "armor.head with minecraft:player_head" in head
        assert "minecraft:profile={id:$(uuid)}" in head, head
        # and the uuid it substitutes is read off the player, not typed
        assert f"data modify storage {NS}:pet uuid set from entity @s UUID" \
            in get
        # the profile component is 1.20.5 syntax, so it lives alone in its own
        # function and a plain head goes on first: an older game loses the skin
        # rather than the entire pet
        assert "armor.head with minecraft:player_head\n" in get, \
            "no plain head, so an older game gets an invisible armour stand"
        assert "[" not in get.split("armor.head with ")[1].split("\n")[0], \
            "the fallback head carries a component, so it fails with the macro"
        assert get.index("armor.head with minecraft:player_head") \
            < get.index(f"function {NS}:pet/head"), "the fallback comes second"
        print("  the head is fitted with /item and the skin is the owner's "
              "own uuid: OK")
    finally:
        shutil.rmtree(d)


def test_the_pet_will_not_start_fights_you_did_not_want():
    """What it attacks is a tag, and what is *not* in it is the design.

    An enderman at the pet's reach is a fight the player is now in, next to
    them. Angering a wolf pack or the piglins is worse than the mob. And a pet
    that kills the cows is not a feature.
    """
    d, info = _built()
    try:
        path = os.path.join(d, "data", NS, "tags", "entity_type", "prey.json")
        vals = json.load(open(path))["values"]
        ids = {v["id"] if isinstance(v, dict) else v for v in vals}
        for never in ("minecraft:enderman", "minecraft:zombified_piglin",
                      "minecraft:wolf", "minecraft:cow", "minecraft:villager",
                      "minecraft:iron_golem", "minecraft:player",
                      "minecraft:armor_stand"):
            assert never not in ids, f"the pet would attack {never}"
        assert "minecraft:zombie" in ids and "minecraft:creeper" in ids
        # one renamed id must not disable the whole tag
        optional = sum(1 for v in vals if isinstance(v, dict)
                       and v.get("required") is False)
        assert optional == len(vals) - len(pet.PREY_CERTAIN)
        print(f"  {len(ids)} kinds of prey, {optional} of them optional so one "
              f"rename cannot disable the pet, and nothing friendly: OK")
    finally:
        shutil.rmtree(d)


def test_the_pet_walks_rather_than_flying_through_walls():
    """It is an entity, so `tp` is right — but only into somewhere it may go."""
    d, _ = _built()
    try:
        step = _text(d, "step")
        moves = [l for l in step.splitlines() if " tp @s" in l]
        assert moves, "the pet never moves"
        for line in moves:
            assert f"#{NS}:passable" in line, \
                f"a step that does not check where it lands: {line}"
        assert any("~1 ~" in l for l in moves), \
            "nothing lets it climb a single step, so a kerb stops it forever"
        # and it must not be a marker, or the world stops holding it up
        assert "Marker:1b" not in _text(d, "get"), \
            "a Marker armour stand has no hitbox, so gravity drops it forever"
        assert "Small:1b" in _text(d, "get"), \
            "a full-size stand puts the head at chest height, not on the ground"
        print(f"  every step is gated on a passable destination, it can climb "
              f"one block, and gravity is the game's job: OK")
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} pet tests\n")
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
