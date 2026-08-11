"""A grappling hook and some movement, because the machine is most of a
kilometre long.

The Mk III is 555 x 195 x 973. Walking from the control wall to the lamp digits
is a minute of holding W down a trench, and the README has admitted for several
sections that this is the worst part of actually using the thing. `go/controls`
and `go/display` fix the two ends. This is for everything in between — reading a
carry chain halfway up, getting onto the roof, crossing the middle without
falling 190 blocks into the floor.

Everything is vanilla, and every mechanic is built from primitives that have
been stable for years. There is no Minecraft in this repository's test rig, so
what cannot be executed can only be reasoned about, and the way to keep that
honest is to lean on old dull commands rather than the newest component syntax:

* the pull does **not** teleport. The first version did — `tp @s` once a tick —
  and the report from a real world was exact: *it just teleports the player
  around*. A player's position set by the server twenty times a second is twenty
  corrections the client has to swallow: no interpolation, no momentum, and
  letting go leaves you hanging because a teleport has no velocity to inherit.
  You cannot set a player's velocity from a command, but you can set an
  entity's, and a rider moves with its vehicle — smoothly, because the client
  interpolates a vehicle between ticks. So the hook mounts you on an invisible
  marker armor stand and steers that;
* the direction is a unit vector obtained without trigonometry, by letting the
  game do it: `facing entity` aims the execution context, `positioned ^ ^ ^1`
  steps one block that way, and the difference between that point and where you
  stand *is* the unit vector;
* the trigger for the dash is `minecraft.used:minecraft.carrot_on_a_stick`, a
  statistic objective, which is the oldest reliable "player right-clicked this"
  signal in the game;
* whether a destination can be entered is a **block tag** this pack ships, so it
  is data rather than a hardcoded list, and the entries that are not certain to
  exist are `required: false` — one renamed block cannot break the tag and
  silently disable the hook.

Two things are deliberate about how it *feels*, since a grappling hook that
works and feels bad is still a bad grappling hook:

**It eases.** The pull accelerates out of rest over about eight ticks and eases
off inside eight blocks of the hook. A constant-speed pull reads as being
dragged by a winch; the ramp reads as swinging.

**It draws the rope.** Six `end_rod` particles between the player's eyes and the
hook, redrawn every tick. Without them the hook is an invisible force and the
whole gadget reads as teleportation.
"""

from __future__ import annotations

import json
import os

from . import mcbuild

#: functions live under this prefix inside the shared namespace
PREFIX = "move"
#: Pull speed in hundredths of a block per tick, so it can live in a scoreboard.
#: 90 is 0.9 blocks a tick — about 18 blocks a second, faster than sprinting and
#: slower than an elytra dive.
PULL_MAX = 90
#: How much of that is gained per tick. Eight ticks from rest to full.
PULL_RAMP = 12
#: Inside this many blocks the pull eases off, so you arrive instead of slam.
EASE_AT = 8
#: ...to this speed. Not zero: a hook that stalls short of the ledge is worse
#: than one that overshoots slightly.
PULL_EASED = 40
#: Let go this close, or the pull fights itself around the hook and jitters.
STOP = 2.5
#: How far a dash carries, checked block by block so it cannot cross a wall.
DASH = 5
#: How far the hook reaches. A bobber lands well short of this; the limit stops
#: someone else's bobber grabbing you in multiplayer.
REACH = 96
#: Ticks of slow falling granted after any gadget lets go of you.
SAFE_FALL = 60


def _passable():
    """What the hook and the dash are allowed to move you into.

    `required: false` on everything but the four air and water ids: a block tag
    containing an id the version does not have fails to load **as a whole**, and
    that failure looks exactly like a grappling hook that does nothing.
    """
    certain = ["minecraft:air", "minecraft:cave_air", "minecraft:void_air",
               "minecraft:water"]
    optional = ["minecraft:short_grass", "minecraft:tall_grass",
                "minecraft:fern", "minecraft:large_fern", "minecraft:vine",
                "minecraft:snow", "minecraft:light", "minecraft:seagrass",
                "minecraft:tall_seagrass", "minecraft:torch",
                "minecraft:redstone_wire", "minecraft:rail",
                "minecraft:cobweb", "minecraft:ladder"]
    return {"values": certain +
            [{"id": i, "required": False} for i in optional]}


def build(outdir, ns=mcbuild.NAMESPACE, machine_help=(), machine_notes=()):
    """Write the traversal half of the pack. Returns a summary dict."""
    fdir = os.path.join(outdir, "data", ns, "function")
    p = PREFIX
    obj_dash = f"{ns}_dash"
    obj_recall = f"{ns}_recall"
    obj_fall = f"{ns}_fall"
    obj_speed = f"{ns}_speed"
    on = f"{ns}_on"
    hover = f"{ns}_hover"
    obj_vec = f"{ns}_vec"
    ride = f"{ns}_ride"
    aim = f"{ns}_aim"
    bobber = (f"@e[type=fishing_bobber,limit=1,sort=nearest,"
              f"distance=..{REACH}]")
    near = lambda d: f"@e[type=fishing_bobber,limit=1,sort=nearest,distance=..{d}]"
    out = {}

    def fn(name, lines):
        out[f"{p}/{name}"] = list(lines)

    def say(text, colour="gray", who="@s"):
        return f'tellraw {who} {{"text":"{text}","color":"{colour}"}}'

    # --- set-up ------------------------------------------------------------
    # From the load tag, so the objectives exist before anything reads them. A
    # `scores={...}` selector against an objective that does not exist is an
    # error every tick, from world load, forever.
    fn("load", [
        f"scoreboard objectives add {obj_dash} "
        f"minecraft.used:minecraft.carrot_on_a_stick",
        f"scoreboard objectives add {obj_recall} "
        f"minecraft.used:minecraft.compass",
        f"scoreboard objectives add {obj_fall} dummy",
        f"scoreboard objectives add {obj_speed} dummy",
        f"scoreboard objectives add {obj_vec} dummy",
    ])

    # --- the gear ----------------------------------------------------------
    # One command gets you everything and switches it on. Three separate steps
    # (`give`, `give`, `on`) is friction for no benefit — nobody wants the rod
    # without the hook enabled.
    #
    # Nothing reads the items' `custom_data`; the pack keys off a player tag
    # instead. A selector that walks into item components is the single most
    # version-fragile thing this could depend on, and if it were wrong the hook
    # would do nothing with no error to say why. The names are decoration, and
    # decoration is allowed to break.
    fn("gear", [
        f'give @s minecraft:fishing_rod[custom_name={{text:"Grappling Hook",'
        f'color:"aqua",italic:false}},custom_data={{{ns}:1b}}]',
        f'give @s minecraft:carrot_on_a_stick[custom_name={{text:"Dash Charm",'
        f'color:"gold",italic:false}},custom_data={{{ns}:1b}}]',
        f'give @s minecraft:compass[custom_name={{text:"Recall Compass",'
        f'color:"light_purple",italic:false}},custom_data={{{ns}:1b}}]',
        # mark where they are standing, so the compass works the first time
        # they press it rather than telling them off for not reading the help
        f"function {ns}:{p}/mark",
        f"function {ns}:{p}/on",
    ])

    fn("on", [
        f"tag @s add {on}",
        f"scoreboard players set @s {obj_dash} 0",
        f"scoreboard players set @s {obj_recall} 0",
        f"scoreboard players set @s {obj_speed} 0",
        say("hook and dash on. Cast the rod to grapple, right-click the "
            "charm to dash, the compass to recall.", "aqua"),
    ])
    fn("off", [
        f"tag @s remove {on}",
        f"tag @s remove {hover}",
        say("hook and dash off."),
    ])
    fn("hover", [
        f"tag @s add {hover}",
        say("hover on — you will not take fall damage. /function "
            f"{ns}:{p}/hover_off to stop.", "aqua"),
    ])
    fn("hover_off", [
        f"tag @s remove {hover}",
        say("hover off."),
    ])

    # --- the tick loop -----------------------------------------------------
    fn("tick", [
        f"execute as @a[tag={on}] at @s run function {ns}:{p}/grapple",
        f"execute as @a[tag={on},scores={{{obj_dash}=1..}}] at @s "
        f"run function {ns}:{p}/dash",
        f"execute as @a[tag={on},scores={{{obj_recall}=1..}}] at @s "
        f"run function {ns}:{p}/recall",
        # the slow-fall window keeps running for a moment after a gadget lets
        # go, which is the whole point: that is when you are falling
        f"execute as @a[scores={{{obj_fall}=1..}}] run "
        f"effect give @s minecraft:slow_falling 1 0 true",
        f"scoreboard players remove @a[scores={{{obj_fall}=1..}}] {obj_fall} 1",
        f"execute as @a[tag={hover}] run "
        f"effect give @s minecraft:slow_falling 2 0 true",
        # a mount whose rider let go, logged out or died would otherwise drift
        # away forever with the motion it was last given
        f"execute as @e[type=armor_stand,tag={ride}] at @s "
        f"unless entity @a[distance=..2] run kill @s",
    ])

    # --- the pull ----------------------------------------------------------
    #
    # This is the second design. The first moved the player with `tp @s` once a
    # tick, and the report from a real world was exact: *it just teleports the
    # player around*. That is what it was. A player's position set by the server
    # twenty times a second is twenty corrections the client has to swallow —
    # there is no interpolation, no momentum, and letting go leaves you hanging
    # in the air because a teleport has no velocity to inherit.
    #
    # You cannot set a player's velocity from a command; the client owns it. But
    # you *can* set an entity's, and a player riding an entity moves with it —
    # smoothly, because the client interpolates a vehicle between ticks, and
    # with momentum, because the motion is real. So the hook mounts you on an
    # invisible marker armor stand and steers that.
    #
    # The direction is a unit vector, and the way to get one without
    # trigonometry is to let the game do it: `facing entity` aims the execution
    # context, `positioned ^ ^ ^1` steps one block that way, and the difference
    # between that point and where you are standing *is* the unit vector. A
    # marker is summoned there for one tick because a position has to belong to
    # an entity before `data get` can read it.
    fn("grapple", [
        f"execute unless entity {bobber} run function {ns}:{p}/release",
        f"execute unless entity {bobber} run return fail",
        f"execute if entity {near(STOP)} run function {ns}:{p}/arrive",
        f"execute if entity {near(STOP)} run return fail",
        # stop against a wall rather than being dragged through it: the mount
        # is a marker and has no collision of its own
        f"execute at @s facing entity {bobber} feet positioned ^ ^ ^1 "
        f"unless block ~ ~ ~ #{ns}:passable run function {ns}:{p}/arrive",
        f"execute at @s facing entity {bobber} feet positioned ^ ^ ^1 "
        f"unless block ~ ~ ~ #{ns}:passable run return fail",
        f"scoreboard players set @s {obj_fall} {SAFE_FALL}",
        f"execute unless entity @e[type=armor_stand,tag={ride},distance=..3] "
        f"run function {ns}:{p}/mount",
        # ease in over eight ticks, and ease off as the hook comes close
        f"scoreboard players add @s {obj_speed} {PULL_RAMP}",
        f"execute if score @s {obj_speed} matches {PULL_MAX}.. run "
        f"scoreboard players set @s {obj_speed} {PULL_MAX}",
        f"execute if entity {near(EASE_AT)} if score @s {obj_speed} matches "
        f"{PULL_EASED + 1}.. run scoreboard players set @s {obj_speed} "
        f"{PULL_EASED}",
        f"execute at @s facing entity {bobber} feet positioned ^ ^ ^1 run "
        f'summon marker ~ ~ ~ {{Tags:["{aim}"]}}',
        f"function {ns}:{p}/thrust",
        f"kill @e[type=marker,tag={aim}]",
        f"function {ns}:{p}/rope",
    ])

    fn("mount", [
        f"summon minecraft:armor_stand ~ ~ ~ {{Invisible:1b,NoGravity:1b,"
        f'Marker:1b,Silent:1b,Tags:["{ride}","{ride}_new"]}}',
        f"ride @s mount @e[type=armor_stand,tag={ride}_new,limit=1]",
        f"tag @e[type=armor_stand,tag={ride}_new] remove {ride}_new",
        f"playsound minecraft:entity.arrow.hit player @s ~ ~ ~ 0.4 1.9",
    ])

    # unit vector times speed, in thousandths, straight onto the vehicle
    axes = list(enumerate("xyz"))
    fn("thrust", [
        f"scoreboard players set #c100 {obj_vec} 100",
        f"scoreboard players operation #spd {obj_vec} = @s {obj_speed}",
    ] + [
        line
        for i, ax in axes
        for line in (
            f"execute store result score #d{ax} {obj_vec} run data get entity "
            f"@e[type=marker,tag={aim},limit=1] Pos[{i}] 1000",
            f"execute store result score #p{ax} {obj_vec} run data get entity "
            f"@s Pos[{i}] 1000",
            f"scoreboard players operation #d{ax} {obj_vec} -= #p{ax} {obj_vec}",
            f"scoreboard players operation #d{ax} {obj_vec} *= #spd {obj_vec}",
            f"scoreboard players operation #d{ax} {obj_vec} /= #c100 {obj_vec}",
            f"execute store result storage {ns}:{p} m{ax} double 0.001 run "
            f"scoreboard players get #d{ax} {obj_vec}",
        )
    ] + [
        f"function {ns}:{p}/motion with storage {ns}:{p}",
    ])
    fn("motion", [
        f"$execute as @s on vehicle run data merge entity @s "
        f"{{Motion:[$(mx)d,$(my)d,$(mz)d]}}",
    ])

    # The rope. Without it the hook is an invisible force and the whole gadget
    # reads as teleportation rather than as a tool.
    fn("rope", [
        f"execute anchored eyes facing entity {bobber} eyes "
        f"positioned ^ ^ ^{d} run particle minecraft:end_rod ~ ~ ~ "
        f"0 0 0 0 1 force @a"
        for d in (1, 2, 3, 4, 5, 6)
    ])

    fn("release", [
        f"scoreboard players set @s {obj_speed} 0",
        f"ride @s dismount",
        f"kill @e[type=armor_stand,tag={ride},distance=..6]",
    ])
    fn("arrive", [
        f"scoreboard players set @s {obj_speed} 0",
        f"scoreboard players set @s {obj_fall} {SAFE_FALL}",
        f"ride @s dismount",
        f"kill @e[type=armor_stand,tag={ride},distance=..6]",
        f"particle minecraft:cloud ~ ~ ~ 0.2 0.2 0.2 0.01 8 normal @a",
        f"playsound minecraft:entity.arrow.hit player @s ~ ~ ~ 0.5 1.8",
    ])

    # An escape hatch, because "you are stuck riding something invisible" is the
    # one failure of this design that a player cannot fix themselves.
    fn("unstick", [
        f"ride @s dismount",
        f"kill @e[type=armor_stand,tag={ride},distance=..32]",
        f"scoreboard players set @s {obj_speed} 0",
        say("dismounted and cleaned up.", "yellow"),
    ])

    # A dash *is* a blink, and is the one place a teleport is the right verb.
    # It is checked block by block so it cannot pass through a wall: each step
    # only happens if the one before it did.
    fn("dash", [
        f"scoreboard players set @s {obj_dash} 0",
        f"scoreboard players set @s {obj_fall} {SAFE_FALL}",
        f"particle minecraft:cloud ~ ~0.2 ~ 0.25 0.1 0.25 0.02 14 normal @a",
        f"playsound minecraft:entity.arrow.shoot player @s ~ ~ ~ 0.7 1.7",
    ] + [
        f"execute positioned ^ ^ ^{i} if block ~ ~ ~ #{ns}:passable "
        f"run tp @s ~ ~ ~"
        for i in range(1, DASH + 1)
    ] + [
        f"particle minecraft:end_rod ~ ~1 ~ 0.1 0.1 0.1 0.01 6 normal @a",
    ])

    # --- the waypoint ------------------------------------------------------
    # Stored in hundredths so the mark is the spot you stood on rather than the
    # block corner, and so a negative coordinate does not truncate towards zero
    # and move the mark a block.
    fn("mark", [
        f"execute store result storage {ns}:wp {axis} double 0.01 run "
        f"data get entity @s Pos[{i}] 100"
        for i, axis in enumerate("xyz")
    ] + [
        f"particle minecraft:happy_villager ~ ~1 ~ 0.3 0.5 0.3 0 12 normal @s",
        say("mark set — right-click the compass to come back.", "light_purple"),
    ])
    fn("recall", [
        f"scoreboard players set @s {obj_recall} 0",
        f"execute unless data storage {ns}:wp x run function {ns}:{p}/no_mark",
        f"execute unless data storage {ns}:wp x run return fail",
        f"scoreboard players set @s {obj_fall} {SAFE_FALL}",
        f"particle minecraft:portal ~ ~1 ~ 0.4 0.6 0.4 0.4 40 normal @a",
        f"function {ns}:{p}/recall_at with storage {ns}:wp",
    ])
    fn("recall_at", [
        f"$tp @s $(x) $(y) $(z)",
        f"particle minecraft:reverse_portal ~ ~1 ~ 0.3 0.5 0.3 0.2 30 normal @a",
        f"playsound minecraft:entity.enderman.teleport player @s ~ ~ ~ 0.6 1.2",
    ])
    fn("no_mark", [
        say(f"no mark yet — /function {ns}:{p}/mark where you want to come "
            f"back to.", "red"),
    ])

    # --- one help, for the whole pack --------------------------------------
    help_lines = [
        f'tellraw @s {{"text":"rscalc — the machine, and getting around it",'
        f'"color":"white"}}',
    ]
    for cmd, what in list(machine_help) + [
        (f"{p}/gear", "hook, dash charm and recall compass — start here"),
        (f"{p}/mark", "remember where you are standing"),
        (f"{p}/hover", "stop taking fall damage while you look around"),
        (f"{p}/unstick", "if the hook ever leaves you riding something"),
        (f"{p}/off", "put the gadgets away"),
    ]:
        help_lines.append(
            f'tellraw @s {{"text":"  /function {ns}:{cmd}  — {what}",'
            f'"color":"gray"}}')
    # the build's speed is a scoreboard value, which is no use to anyone who
    # cannot see it. Both of these were knobs that existed and were untellable.
    for note in machine_notes:
        help_lines.append(
            f'tellraw @s {{"text":"  {note}","color":"dark_gray"}}')
    help_lines.append(
        f'tellraw @s {{"text":"  Cast the rod to grapple — it pulls while the '
        f'bobber is out and lets go when you reel in. The charm dashes {DASH} '
        f'blocks where you look. Both leave you with slow falling.",'
        f'"color":"gray"}}')
    out["help"] = help_lines

    for name, lines in out.items():
        path = os.path.join(fdir, f"{name}.mcfunction")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    tdir = os.path.join(outdir, "data", ns, "tags", "block")
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, "passable.json"), "w") as f:
        json.dump(_passable(), f, indent=1)

    return {"functions": sorted(out), "prefix": p,
            "pull": PULL_MAX / 100, "dash": DASH, "reach": REACH}


def write_hooks(outdir, ns=mcbuild.NAMESPACE, on_load=(), on_tick=()):
    """The `minecraft:load` and `minecraft:tick` tags that drive the gadgets.

    `on_load` is for functions this module does not own — the machine half's
    `sys/keep`, which puts its chunks back under force-load on every world
    start. It is a parameter rather than a hardcoded name because the gadgets
    can be written without the machine, and a tag hooking a function that is
    not in the pack is exactly what the linter is for.
    """
    d = os.path.join(outdir, "data", "minecraft", "tags", "function")
    os.makedirs(d, exist_ok=True)
    # the machine's own hooks run first, so the chunks are back before anything
    # else looks at them
    for hook, values in (("load", list(on_load) + [f"{ns}:{PREFIX}/load"]),
                         ("tick", [f"{ns}:{PREFIX}/tick"] + list(on_tick))):
        with open(os.path.join(d, f"{hook}.json"), "w") as f:
            json.dump({"values": values}, f, indent=1)


#: What each landmark teleport is for, in a player's words. `go/above` reading
#: "teleport to the above" was the generated phrasing, and it says nothing.
GO_HELP = {
    "above": "look down on the whole machine from the air",
    "controls": "the labelled lever wall — set A, B and the operation here",
    # no distance here: the same table serves the Mk II console and the ALU,
    # where "973 blocks away" would simply be a lie
    "display": "the seven-segment readout, at the far end",
    "origin": "the -X -Y -Z corner the build was placed from",
}


def attach(outdir, ns, paced):
    """Wire the gadgets into a machine pack. One pack, assembled in one place.

    Two tools write this pack: `tools/build_world.py` writes the one you
    download, and `tools/build_preview.py` writes the same one into the page's
    bundle. They used to assemble this half separately — the same help lines,
    the same hook tags, typed twice — and every check compared the page against
    the bundle, which is the same copy, so four differences sat there unseen:
    the page's pack had no world-start hook, no pace note, no landmark
    descriptions and a stale `go/...` phrasing. This is that code, once.

    `paced` is what `mcbuild.write_paced_entry` returned.
    """
    machine_help = [
        ("build", "place it, from the -X -Y -Z corner"),
        ("clear", "take it away again, from anywhere"),
        ("status", "where it is and what it is doing"),
        ("load", "force-load its chunks so it ticks"),
        ("unload", "release them again"),
        ("abort", "stop a build or clear part way"),
    ] + [(f"go/{k}", GO_HELP.get(k, f"teleport to the {k}"))
         for k in sorted(paced["landmarks"])]
    ticks = paced["ticks"]
    notes = [
        f"To watch it go up slowly: /scoreboard players set #pace {ns}_v 4 "
        f"before /function {ns}:build — that is ticks per batch, so 4 makes "
        f"the {ticks} batches take {ticks * 4 / 20:.0f} seconds instead of "
        f"{ticks / 20:.0f}.",
    ]
    # only if there are any: the Mk II console and the bare ALU go through this
    # same function, and a help line promising signs on a wall that has none is
    # worse than no help line
    if paced["signs"]:
        where = (f" /function {ns}:go/controls puts you in front of them."
                 if "controls" in paced["landmarks"] else "")
        notes.append(
            f"Each of the {paced['signs']} controls has a sign beside it "
            f"saying which bit or operation it is.{where}")
    from . import pet
    creature = pet.build(outdir, ns=ns)
    move = build(outdir, ns=ns, machine_help=machine_help + pet.HELP,
                 machine_notes=notes)
    write_hooks(outdir, ns=ns,
                on_load=[paced["on_world_load"].split(" ", 1)[1],
                         f"{ns}:{pet.PREFIX}/load"],
                on_tick=[f"{ns}:{pet.PREFIX}/tick"])
    move["pet"] = creature
    return move
