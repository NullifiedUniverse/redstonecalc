"""The game's own grammar, against this project's commands.

`rscalc/mccheck.py` reads the Brigadier tree Mojang generates and walks each
command through it the way the game does. Its worth rests on one claim — that it
agrees with the game — so most of this file is that claim, tested:

  * against *recorded verdicts*: `tests/data/oracle_26.2.json.gz` holds twelve
    thousand commands, with the answer the game's own function compiler gave to
    each (`tools/oracle.py golden` writes it). No JDK is needed to check it.
  * against a bug that really shipped, reintroduced.
  * against the assembled pack, on every version whose grammar is vendored.

The failure this all guards is described in DESIGN §38: one command in a
function that does not parse gets the whole function rejected at load, and from
in the game that is indistinguishable from a missing file.
"""

import gzip
import json
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from rscalc import mccheck, mcbuild, pet, traverse

NS = mcbuild.NAMESPACE
GOLDEN = os.path.join(os.path.dirname(__file__), "data", "oracle_26.2.json.gz")

#: Where the checker is deliberately stricter than the game. Each is a thing the
#: pack never writes, refused because it is a bug class or because an older
#: grammar may not tolerate it. Recorded so the strictness is a decision.
STRICTER = (",]", ",}", "$(")


def _pack():
    """A real, assembled pack: the exporter's, the gadgets', the pet's."""
    from test_mcbuild import _sample_world
    d = tempfile.mkdtemp()
    info = mcbuild.export_datapack(_sample_world(), d, name="t", per_file=40)
    traverse.attach(d, info["namespace"], info)
    return d


def test_the_checker_refuses_what_the_game_refuses():
    """Hand-picked, and each one a way a command has actually gone wrong."""
    g = mccheck.load("26.2")
    good = [
        "bossbar set rscalc:progress color blue",
        "setblock ~1 ~2 ~3 minecraft:repeater[delay=4,facing=east,locked=false,powered=false] replace",
        "tp @s ^ ^0.55 ^0.22",
        "kill @e[type=marker,tag=x,distance=..2.5,limit=1,sort=nearest]",
        "data get entity @e[type=marker,limit=1] Pos[0] 1000",
        "execute as @a at @s run playsound minecraft:block.stone.place master @s ~ ~ ~ 0.22 1.4",
        "execute if loaded ~ ~ ~ run say hi",
        "scoreboard objectives add a minecraft.used:minecraft.carrot_on_a_stick",
        'tellraw @a {"text":"x","color":"aqua"}',
        "effect give @s minecraft:slow_falling 1 0 true",
        "execute store result score #a rscalc_v run data get entity @s Pos[0] 1000",
    ]
    bad = {
        # the one that shipped: a TEXT colour where a boss-bar colour belongs
        "bossbar set rscalc:progress color aqua": "blue",
        "setblock ~1 ~2 ~3 minecraft:repeater[delay=5] replace": "delay",
        "setblock ~1 ~2 ~3 minecraft:not_a_block replace": "not a block",
        "tp @s ^ ~1 ^0.22": None,                       # local and world mixed
        "kill @e[type=notathing]": "entity type",
        "kill @e[type=marker,limit=0]": "limit",
        "data get entity @e[type=marker] Pos[0]": "ONE entity",
        "kill ~": "name",                               # 306 corpus cases
        "clear rscalc:tick": None,                      # a name, not a function
        "tellraw @e[type=zombie] {\"text\":\"x\"}": "players only",
        'tellraw @a {"text":"x","color":"mauve"}': "colour",
        "scoreboard objectives add a minecraft.used:minecraft.not_an_item": "criterion",
        "effect give @s minecraft:not_an_effect 1 0 true": "mob_effect",
        "execute if score @e rscalc_v matches 1.. run say hi": "ONE entity",
        "data get entity @s Pos[0": "index",
        "bossbar add rscalc:progress -1": "component",
        "$say hi": "macro",                              # `$` with no $(...)
        "damage @s 4 minecraft:stone": None,             # not a damage type
        "particle minecraft:not_a_particle ~ ~ ~ 0 0 0 0 1": "particle",
    }
    for cmd in good:
        why = g.check(cmd)
        assert why is None, f"the game accepts {cmd!r} but mccheck says: {why}"
    for cmd, needle in bad.items():
        why = g.check(cmd)
        assert why is not None, f"the game refuses {cmd!r}; mccheck accepted it"
        if needle:
            assert needle.lower() in why.lower(), (cmd, why)
    print(f"  {len(good)} commands accepted and {len(bad)} refused, each for "
          f"the game's own reason: OK")


def test_the_checker_agrees_with_the_games_own_verdicts():
    """Twelve thousand commands, each with the game's recorded answer.

    These were produced by `CommandFunction.fromLines` — the call the server
    makes when a pack loads — on the real 26.2 jar, and the set is the pack's
    commands plus mutations of them: junk substituted for a token, tokens
    deleted, swapped and truncated, and single characters edited. Zero false
    accepts is the hard requirement; a command mccheck waves through that the
    game refuses is exactly the bug this exists to prevent.
    """
    with gzip.open(GOLDEN, "rt", encoding="utf-8") as f:
        gold = json.load(f)
    g = mccheck.load(gold["version"])
    g.macros = gold["macros"]
    false_accept, false_refuse = [], []
    for line, ok in gold["cases"]:
        mine = g.check(line) is None
        if mine and not ok:
            false_accept.append(line)
        elif ok and not mine:
            false_refuse.append(line)
    assert not false_accept, (
        f"{len(false_accept)} commands the game refuses but mccheck accepts, "
        f"e.g. {false_accept[:3]}")
    unexplained = [l for l in false_refuse if not any(m in l for m in STRICTER)]
    assert not unexplained, (
        f"{len(unexplained)} commands the game accepts but mccheck refuses, "
        f"and it is not one of the deliberate strictnesses: {unexplained[:3]}")
    n = len(gold["cases"])
    refused = sum(1 for _, ok in gold["cases"] if not ok)
    print(f"  {n:,} of the game's own verdicts ({refused:,} refusals): no false "
          f"accepts, {len(false_refuse)} deliberate strictnesses: OK")


def test_the_checker_never_crashes_on_bad_input():
    """A checker that crashes is skipped, or stops a build for the wrong reason.

    The corpus that found the last crash used single-character edits, so this
    does the same: every mutation of every command the exporter, the gadgets
    and the pet emit must come back as an answer, never as an exception and
    never as `internal error`.
    """
    d = _pack()
    try:
        g = mccheck.load("26.2")
        base = []
        for dp, _, files in os.walk(os.path.join(d, "data", NS, "function")):
            for f in files:
                if f.endswith(".mcfunction") and "part" not in dp:
                    for line in open(os.path.join(dp, f), encoding="utf-8"):
                        if line.strip() and not line.startswith("#"):
                            base.append(line.strip())
        rng = random.Random(4)
        chars = list("abz09_-.:~^@#[]{}=,!*\"' ")
        n = 0
        for line in base:
            for _ in range(6):
                i = rng.randrange(len(line))
                k = rng.choice("dri")
                c = rng.choice(chars)
                m = (line[:i] + line[i + 1:] if k == "d" else
                     line[:i] + c + line[i + 1:] if k == "r" else
                     line[:i] + c + line[i:])
                why = g.check(m)
                assert not (why or "").startswith("internal error"), (m, why)
                n += 1
        print(f"  {n:,} mutated commands, every one answered without crashing: OK")
    finally:
        shutil.rmtree(d)


def test_the_bug_that_shipped_is_refused():
    """`bossbar set … color aqua`: one wrong word, one function that never loads.

    `aqua` is a text colour; a boss bar takes one of seven others. The game
    rejected the whole of `rscalc:build`, which is the one command the README
    tells a player to type, while all 165 other functions worked. Putting it back
    has to be refused, in every version, with the game's own list of colours.
    """
    d = _pack()
    try:
        path = os.path.join(d, "data", NS, "function", "build.mcfunction")
        body = open(path).read()
        assert "progress color blue" in body
        open(path, "w").write(body.replace("progress color blue",
                                           "progress color aqua"))
        for v in mccheck.versions():
            found = mccheck.problems(d, v)
            assert any(f == f"{NS}:build" and "blue" in why
                       for f, _, _, why in found), (v, found[:2])
        print(f"  the boss-bar `aqua` is refused on all {len(mccheck.versions())} "
              f"versions, naming the seven real colours: OK")
    finally:
        shutil.rmtree(d)


def test_the_assembled_pack_loads_where_it_claims_to():
    """Floors, measured: the exporter's commands, the gadgets and the pet.

    Nothing about this is asserted from memory. For every version whose grammar
    is vendored, every command of the assembled pack is walked through that
    version's own tree. What comes out is exact and a little humbling: the
    machine needs `return fail` (1.20.3), and the pet's skin needs item
    components (1.20.5) — which is why it lives in its own function and degrades
    to a plain head rather than taking the pet with it.
    """
    d = _pack()
    try:
        res = mccheck.floors(d, optional=[f"{NS}:{f}" for f in pet.OPTIONAL])
        per = res["per_version"]
        assert res["core"] == "1.20.3", (res["core"], per.get(res["core"]))
        assert res["full"] == "1.20.5", (res["full"], per.get("1.20.5"))
        assert res["newest"] == mccheck.versions()[-1], res["newest"]
        # exactly one thing separates 1.20.3 from clean: the pet's skin
        assert {p[0] for p in per["1.20.3"]} == {f"{NS}:{f}" for f in pet.OPTIONAL}
        # and 1.20.2 fails for exactly two reasons: `return fail` only arrived
        # in 1.20.3, and the pet's skin (which is optional) needs components
        skin = {f"{NS}:{f}" for f in pet.OPTIONAL}
        # (the command text is cut to 90 characters; the reason keeps the context)
        assert all("return" in p[3] or p[0] in skin for p in per["1.20.2"]), \
            [p for p in per["1.20.2"] if "return" not in p[3] and p[0] not in skin][:2]
        assert any("return" in p[3] for p in per["1.20.2"])
        # every version from the floor up, including the newest, is clean
        assert not per["26.2"] and not per[mccheck.versions()[-1]]
        print(f"  loads from {res['core']} (the pet's skin from {res['full']}) "
              f"through {res['newest']}; the only thing 1.20.3 refuses is "
              f"pet/head: OK")
    finally:
        shutil.rmtree(d)


def test_pack_mcmeta_claims_exactly_what_was_measured():
    """The format range is an output of the measurement, not a typed-in claim.

    A version outside `supported_formats` is not refused, it is *warned about*,
    which reads as "this pack is broken" to a player; and one inside it that the
    pack does not actually load on would be a lie. Both directions are checked
    against the game's own reported data-pack formats.
    """
    d = _pack()
    try:
        meta = json.load(open(os.path.join(d, "pack.mcmeta")))["pack"]
        res = mccheck.floors(d, optional=[f"{NS}:{f}" for f in pet.OPTIONAL])
        assert meta["min_format"] == mccheck.format_of(res["core"]), meta
        assert meta["max_format"] == mccheck.format_of(res["newest"]), meta
        assert meta["supported_formats"] == {
            "min_inclusive": meta["min_format"],
            "max_inclusive": meta["max_format"]}, meta
        assert all(isinstance(meta[k], int)
                   for k in ("pack_format", "min_format", "max_format")), meta
        # the game reports these itself; the table in mcbuild is not the source
        for v, fmt in mcbuild.MC_FORMATS.items():
            if v in mccheck.versions():
                assert mccheck.format_of(v) == fmt[0], (v, fmt)
        print(f"  pack.mcmeta claims data formats {meta['min_format']}.."
              f"{meta['max_format']}, which is what the grammar measured, and "
              f"mcbuild's format table matches the game's own: OK")
    finally:
        shutil.rmtree(d)


def test_tag_files_name_things_that_exist():
    """A tag naming an id the version lacks does not lose the entry — it loses
    the **tag**. `#rscalc:passable` failing to load is a grappling hook that
    does nothing, so every entry not marked `required: false` has to be real on
    every version the pack claims."""
    d = _pack()
    try:
        for v in mccheck.versions():
            assert not mccheck.tag_problems(d, v), (v, mccheck.tag_problems(d, v))
        # break it: a required id that does not exist
        path = os.path.join(d, "data", NS, "tags", "entity_type", "prey.json")
        tag = json.load(open(path))
        tag["values"].append("minecraft:not_a_mob")
        json.dump(tag, open(path, "w"))
        found = mccheck.tag_problems(d, "26.2")
        assert found and "not_a_mob" in found[0][1], found
        # and an optional one is allowed to be absent
        tag["values"][-1] = {"id": "minecraft:not_a_mob", "required": False}
        json.dump(tag, open(path, "w"))
        assert not mccheck.tag_problems(d, "26.2")
        print(f"  every required tag entry is real on all "
              f"{len(mccheck.versions())} versions, and a bad one is caught: OK")
    finally:
        shutil.rmtree(d)


def test_the_vendored_grammar_is_what_the_game_generated():
    """`vendor/mc/` is distilled from the servers themselves, so spot-check that
    it says what the game says about the things this project leans on."""
    g = mccheck.load("26.2")
    tree = g.tree["children"]
    colours = set(tree["bossbar"]["children"]["set"]["children"]["id"]
                  ["children"]["color"]["children"])
    assert colours == {"blue", "green", "pink", "purple", "red", "white",
                       "yellow"}, colours
    assert "aqua" not in colours
    # `return fail` is the reason the floor is 1.20.3, and it is a literal in
    # the tree on 1.20.3 and absent on 1.20.2
    assert "fail" in mccheck.load("1.20.3").tree["children"]["return"]["children"]
    assert "fail" not in mccheck.load("1.20.2").tree["children"]["return"]["children"]
    assert "minecraft:player_head" in g.regs["minecraft:item"]
    assert g.blocks["minecraft:repeater"]["delay"] == ["1", "2", "3", "4"]
    print(f"  the vendored trees say what the game says: {len(mccheck.versions())} "
          f"versions, boss-bar colours are {sorted(colours)}: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} grammar tests\n")
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
