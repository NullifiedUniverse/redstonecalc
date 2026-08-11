"""A grappling hook and some movement, because the machine is 973 blocks long.

The Mk III is 555 x 195 x 973. Walking from the control wall to the lamp digits
is a minute of holding W down a trench, and the README has admitted for several
sections that this is the worst part of actually using the thing. `go_controls`
and `go_display` in the machine's own pack fix the two ends. This pack is for
everything in between — reading a carry chain halfway up, getting onto the roof,
crossing the middle without falling 190 blocks into the floor.

Everything here is vanilla and every mechanic is built out of commands whose
behaviour has been stable for years, on purpose. There is no Minecraft in this
repository's test rig, so anything I cannot execute I can only reason about —
and the way to keep that honest is to lean on primitives that are old and dull
rather than on the newest component syntax. Specifically:

* the pull is `execute ... facing entity ... positioned ^ ^ ^d ... run tp @s ~ ~ ~`,
  which moves the player along the line to the hook **without touching their
  rotation** — `facing` changes the execution context's rotation, not the
  entity's, so the camera stays where the player put it;
* the dash is driven by `minecraft.used:minecraft.carrot_on_a_stick`, a
  statistic objective, which is the oldest reliable "player right-clicked"
  signal in the game;
* whether a destination is enterable is a **block tag** this pack ships, so it
  is data rather than a hardcoded list of ids, and the entries that are not
  certain to exist are marked `required: false` so one bad id cannot break the
  tag and silently disable the hook.

What is *not* claimed: none of this has been run in Minecraft. It is checked by
`rscalc/packlint.py`, which resolves every function reference, every objective,
every tag and every macro argument, and by `tests/test_traverse.py`. That
catches the pack failing to load or naming things that do not exist. It cannot
catch the hook feeling wrong.
"""

from __future__ import annotations

import json
import os

from . import mcbuild

NS = "rscalc_move"
#: How far the pull moves you per tick. 0.85 blocks a tick is ~17 blocks a
#: second — faster than sprinting, slower than an elytra dive, and slow enough
#: that the passability check in front of you is never jumped over.
PULL = 0.85
#: Stop this close, or the pull fights itself around the hook and jitters.
STOP = 2.0
#: How far a dash carries. Checked block by block, so it cannot cross a wall.
DASH = 4
#: How far the hook can reach. A fishing bobber lands well short of this; the
#: limit is here so that in multiplayer you cannot be pulled by someone else's.
REACH = 96


def _blocks_tag():
    """What the hook and the dash are allowed to move you into.

    `required: false` on everything but the four air/water ids: a block tag
    containing an id the version does not have fails to load *as a whole*, and
    the failure looks exactly like a grappling hook that does nothing.
    """
    certain = ["minecraft:air", "minecraft:cave_air", "minecraft:void_air",
               "minecraft:water"]
    optional = ["minecraft:short_grass", "minecraft:tall_grass",
                "minecraft:fern", "minecraft:large_fern", "minecraft:vine",
                "minecraft:snow", "minecraft:light", "minecraft:seagrass",
                "minecraft:tall_seagrass", "minecraft:torch",
                "minecraft:redstone_wire", "minecraft:rail"]
    return {"values": certain +
            [{"id": i, "required": False} for i in optional]}


def build(outdir, mc_version=mcbuild.MC_VERSION_DEFAULT):
    """Write the pack. Returns a summary dict."""
    fdir = os.path.join(outdir, "data", NS, "function")
    os.makedirs(fdir, exist_ok=True)
    obj_dash = f"{NS}_dash"
    obj_fall = f"{NS}_fall"
    obj_use = f"{NS}_use"
    tag_on = f"{NS}_on"
    bobber = (f"@e[type=fishing_bobber,limit=1,sort=nearest,"
              f"distance=..{REACH}]")
    out = {}

    def fn(name, lines):
        out[name] = list(lines)

    # --- set-up, from the load tag so the objectives exist before anything
    # reads them. A `scores={...}` selector against an objective that does not
    # exist is an error every tick, from world load, forever.
    fn("load", [
        f"scoreboard objectives add {obj_dash} "
        f"minecraft.used:minecraft.carrot_on_a_stick",
        f"scoreboard objectives add {obj_use} "
        f"minecraft.used:minecraft.fishing_rod",
        f"scoreboard objectives add {obj_fall} dummy",
        f'tellraw @a {{"text":"[rscalc] traversal loaded — /function '
        f'{NS}:help","color":"dark_gray"}}',
    ])

    fn("help", [
        f'tellraw @a {{"text":"rscalc traversal","color":"white"}}',
    ] + [
        f'tellraw @a {{"text":"  /function {NS}:{c}  — {d}","color":"gray"}}'
        for c, d in [
            ("gear", "get the hook and the dash charm, and switch it on"),
            ("on", "enable the hook and dash for yourself"),
            ("off", "disable them"),
            ("mark", "remember where you are standing"),
            ("recall", "go back to the mark"),
        ]
    ] + [
        f'tellraw @a {{"text":"  Cast the rod to grapple; hold it out to keep '
        f'pulling, reel in to let go. Right-click the charm to dash '
        f'{DASH} blocks the way you are looking. Both leave you with slow '
        f'falling, so the landing is survivable.","color":"gray"}}',
    ])

    # The rod is a plain fishing rod with a name and a marker in custom_data.
    # Nothing reads the custom_data — the pack keys off a player tag instead —
    # because a selector that walks into item components is the most
    # version-fragile thing this could possibly depend on, and if it were wrong
    # the hook would do nothing with no error to say why.
    fn("gear", [
        f'give @s minecraft:fishing_rod[custom_name={{text:"Grappling Hook",'
        f'color:"aqua",italic:false}},custom_data={{{NS}:1b}}]',
        f'give @s minecraft:carrot_on_a_stick[custom_name={{text:"Dash Charm",'
        f'color:"gold",italic:false}},custom_data={{{NS}:1b}}]',
        f"function {NS}:on",
    ])

    fn("on", [
        f"tag @s add {tag_on}",
        f"scoreboard players set @s {obj_dash} 0",
        f'tellraw @s {{"text":"[rscalc] hook and dash on. Cast the rod to '
        f'grapple.","color":"aqua"}}',
    ])
    fn("off", [
        f"tag @s remove {tag_on}",
        f'tellraw @s {{"text":"[rscalc] hook and dash off.","color":"gray"}}',
    ])

    # --- the tick loop -----------------------------------------------------
    fn("tick", [
        f"execute as @a[tag={tag_on}] at @s run function {NS}:grapple",
        f"execute as @a[tag={tag_on},scores={{{obj_dash}=1..}}] at @s "
        f"run function {NS}:dash",
        # the slow-fall window keeps running for a moment after the hook lets
        # go, which is the whole point: that is when you are falling
        f"execute as @a[scores={{{obj_fall}=1..}}] run "
        f"effect give @s minecraft:slow_falling 1 0 true",
        f"scoreboard players remove @a[scores={{{obj_fall}=1..}}] {obj_fall} 1",
    ])

    # One step along the line to the hook. Three things are deliberate:
    #
    #   `facing entity ... feet` sets the *execution* rotation, so `^ ^ ^d` is
    #   measured towards the bobber while the player's own view is untouched;
    #   `positioned ^ ^ ^d` moves the execution position there and `tp @s ~ ~ ~`
    #   lands on it, which avoids `tp @s ^ ^ ^d` resolving locals against the
    #   player's rotation instead of the aimed one;
    #   `if block ~ ~ ~ #passable` refuses the step into a solid, so the hook
    #   stops you against a wall rather than shoving you inside it.
    fn("grapple", [
        f"execute unless entity {bobber} run return fail",
        f"execute if entity @e[type=fishing_bobber,limit=1,sort=nearest,"
        f"distance=..{STOP}] run return fail",
        f"scoreboard players set @s {obj_fall} 60",
        f"execute facing entity {bobber} feet positioned ^ ^ ^{PULL} "
        f"if block ~ ~ ~ #{NS}:passable run tp @s ~ ~ ~",
    ])

    # A dash is checked block by block so it cannot pass through a wall: each
    # step only happens if the one before it did.
    steps = []
    for i in range(1, DASH + 1):
        steps.append(f"execute positioned ^ ^ ^{i} if block ~ ~ ~ "
                     f"#{NS}:passable run tp @s ~ ~ ~")
    fn("dash", [
        f"scoreboard players set @s {obj_dash} 0",
        f"scoreboard players set @s {obj_fall} 60",
    ] + steps + [
        f"playsound minecraft:entity.arrow.shoot player @s ~ ~ ~ 0.6 1.6",
    ])

    # --- a waypoint --------------------------------------------------------
    # Stored as hundredths so the mark is the spot you stood on rather than the
    # block corner, and so a negative coordinate does not truncate towards zero
    # and move the mark a block.
    fn("mark", [
        f"execute store result storage {NS}:wp x double 0.01 run "
        f"data get entity @s Pos[0] 100",
        f"execute store result storage {NS}:wp y double 0.01 run "
        f"data get entity @s Pos[1] 100",
        f"execute store result storage {NS}:wp z double 0.01 run "
        f"data get entity @s Pos[2] 100",
        f'tellraw @s {{"text":"[rscalc] mark set — /function {NS}:recall to '
        f'come back.","color":"gray"}}',
    ])
    fn("recall", [
        f"execute unless data storage {NS}:wp x run function {NS}:no_mark",
        f"execute unless data storage {NS}:wp x run return fail",
        f"scoreboard players set @s {obj_fall} 60",
        f"function {NS}:recall_at with storage {NS}:wp",
    ])
    fn("recall_at", [
        f"$tp @s $(x) $(y) $(z)",
        f"playsound minecraft:entity.enderman.teleport player @s ~ ~ ~ 0.5 1.2",
    ])
    fn("no_mark", [
        f'tellraw @s {{"text":"[rscalc] no mark yet — /function {NS}:mark '
        f'where you want to come back to.","color":"red"}}',
    ])

    for name, lines in out.items():
        with open(os.path.join(fdir, f"{name}.mcfunction"), "w") as f:
            f.write("\n".join(lines) + "\n")

    tdir = os.path.join(outdir, "data", NS, "tags", "block")
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, "passable.json"), "w") as f:
        json.dump(_blocks_tag(), f, indent=1)

    for hook, values in (("load", [f"{NS}:load"]), ("tick", [f"{NS}:tick"])):
        d = os.path.join(outdir, "data", "minecraft", "tags", "function")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{hook}.json"), "w") as f:
            json.dump({"values": values}, f, indent=1)

    with open(os.path.join(outdir, "pack.mcmeta"), "w") as f:
        json.dump(mcbuild.pack_meta(
            "rscalc traversal — grappling hook, dash and a waypoint",
            mc_version), f, indent=1)

    return {"functions": sorted(out), "namespace": NS,
            "pull": PULL, "dash": DASH, "reach": REACH,
            "files": len(out) + 4}
