"""A pet head that walks the path you walked, and bites things.

It is a player head sitting on the ground. It follows you by **retracing your
route** rather than beelining at you: every metre or so you drop a breadcrumb,
and the pet walks to the oldest one it can still see, then the next. That is the
difference between a companion and a magnet — a magnet swims into the wall
between you, and this thing goes round the corner you went round.

Three decisions are load-bearing, and each one is a lesson from the grappling
hook in `traverse.py`:

**It is an entity, so it may be teleported.** The hook could not use `tp`,
because teleporting a *player* twenty times a second is twenty corrections the
client has to swallow and it reads as stuttering. A non-player entity is the
opposite case: the client is *sent* entity positions and interpolates between
them, so `tp @s ^ ^ ^0.2` once a tick is exactly how smooth entity movement is
meant to be done.

**Vanilla physics carries it, not us.** The armour stand is not a `Marker`, so
it keeps its hitbox, collides with the world, and falls and stands on the ground
with no code from us at all. We drive one axis — forwards — and let the game do
gravity, which is both less code and less to be wrong about.

**The head goes on with `/item`, not with NBT.** Entity equipment moved from
`ArmorItems` to `equipment` in 1.21.5, and an item's contents moved from NBT to
components in 1.20.5. A `summon` carrying either spelling is a pack that works
on one side of a version line and silently produces a headless invisible armour
stand on the other. `/item replace entity` has meant the same thing throughout.
"""

from __future__ import annotations

import json
import os

from . import mcbuild

#: functions live under this prefix inside the shared namespace
PREFIX = "pet"
#: How fast the pet walks, in blocks per tick. A sprinting player does about
#: 0.28, so this keeps up with walking and loses ground to a sprint — which is
#: what makes it read as following rather than sticking to you.
SPEED = 0.22
#: Drop a breadcrumb when the last one is at least this far behind. Closer than
#: this and the trail is thousands of markers; further and the pet cuts corners
#: through the wall the trail was there to avoid.
CRUMB_GAP = 1.5
#: Reaching a crumb means getting this close to it.
CRUMB_HIT = 0.9
#: Longest trail kept. At one crumb per 1.5 blocks that is about 100 blocks of
#: history, which is enough to walk a corridor and come back.
TRAIL = 64
#: Past this the trail is no help — the pet has been left behind or the player
#: teleported — so it gives up and comes to you.
LOST = 48
#: How far the pet bites, and how hard, and how often (in ticks).
BITE_REACH = 3.5
BITE_DAMAGE = 4
BITE_COOL = 20

#: What the pet is willing to attack.
#:
#: Everything past the first two is `required: false`, for the same reason the
#: hook's passable tag is: an entity type tag naming an id the version does not
#: have fails to load **as a whole**, and a pet that never attacks anything
#: looks like a broken pet rather than a missing block id.
#:
#: Deliberately absent: `enderman` (attacking one starts a fight you did not
#: want, at the pet's reach, next to you), `zombified_piglin` and `wolf` (angering
#: a pack is worse than the mob), and every passive mob. A pet that kills the
#: cows is not a feature.
PREY_CERTAIN = ["minecraft:zombie", "minecraft:skeleton"]
PREY_OPTIONAL = [
    "minecraft:creeper", "minecraft:spider", "minecraft:cave_spider",
    "minecraft:husk", "minecraft:drowned", "minecraft:stray",
    "minecraft:bogged", "minecraft:witch", "minecraft:pillager",
    "minecraft:vindicator", "minecraft:evoker", "minecraft:ravager",
    "minecraft:vex", "minecraft:phantom", "minecraft:slime",
    "minecraft:magma_cube", "minecraft:blaze", "minecraft:silverfish",
    "minecraft:endermite", "minecraft:guardian", "minecraft:elder_guardian",
    "minecraft:hoglin", "minecraft:zoglin", "minecraft:piglin_brute",
    "minecraft:wither_skeleton", "minecraft:breeze", "minecraft:creaking",
    "minecraft:shulker", "minecraft:illusioner",
]


def _prey():
    return {"values": PREY_CERTAIN +
            [{"id": i, "required": False} for i in PREY_OPTIONAL]}


def build(outdir, ns=mcbuild.NAMESPACE):
    """Write the pet half of the pack. Returns a summary dict."""
    fdir = os.path.join(outdir, "data", ns, "function")
    p = PREFIX
    obj = f"{ns}_pet"            # which pet belongs to which player
    seq = f"{ns}_seq"            # breadcrumb order
    cool = f"{ns}_bite"          # attack cooldown
    pet = f"{ns}_pet"            # tag on the armour stand
    crumb = f"{ns}_crumb"
    mine = f"{ns}_mine"          # "belongs to the player running this"
    boss = f"{ns}_boss"          # ...and that player, so the pet can find them
    target = f"{ns}_target"
    store = f"{ns}:pet"
    out = {}

    def fn(name, lines):
        out[f"{p}/{name}"] = list(lines)

    def say(text, colour="gray", who="@s"):
        return f'tellraw {who} {{"text":"{text}","color":"{colour}"}}'

    # --- set-up -------------------------------------------------------------
    fn("load", [
        f"scoreboard objectives add {obj} dummy",
        f"scoreboard objectives add {seq} dummy",
        f"scoreboard objectives add {cool} dummy",
    ])

    # --- adopting one -------------------------------------------------------
    #
    # The head is *your* head. A player's skin is looked up from the profile
    # component's uuid, and a player's uuid is the one piece of identity that can
    # be read straight off the entity — `data get entity @s Name` does not exist
    # for players, and asking for a name would mean asking the player to type it.
    fn("get", [
        f"function {ns}:{p}/dismiss",
        # a fresh id, so this pet is told apart from everyone else's
        f"scoreboard players add #next {obj} 1",
        f"scoreboard players operation @s {obj} = #next {obj}",
        f"data modify storage {store} uuid set from entity @s UUID",
        # Small, so the head sits on the ground rather than floating at chest
        # height. Not a Marker: it keeps its hitbox, so gravity and collision
        # are the game's problem and not ours. Invulnerable and slot-locked so
        # nothing can kill it or take the head off it.
        f"summon minecraft:armor_stand ~ ~ ~ {{Small:1b,Invisible:1b,"
        f"Invulnerable:1b,NoBasePlate:1b,PersistenceRequired:1b,Silent:1b,"
        f'DisabledSlots:4144959,Tags:["{pet}","{pet}_new"]}}',
        f"function {ns}:{p}/head with storage {store}",
        f"execute as @e[type=armor_stand,tag={pet}_new] run "
        f"scoreboard players operation @s {obj} = #next {obj}",
        f"tag @e[type=armor_stand,tag={pet}_new] remove {pet}_new",
        f"playsound minecraft:entity.player.levelup player @s ~ ~ ~ 0.6 1.6",
        say("your head is following you. It walks the way you walked, and "
            "bites what comes near. /function " + ns + ":pet/dismiss to put "
            "it away.", "green"),
    ])
    # `/item replace` rather than an `ArmorItems` or `equipment` tag on the
    # summon: those two spellings changed sides in 1.21.5, and the wrong one
    # fails silently — an invisible armour stand with no head is a pet you
    # cannot see at all.
    fn("head", [
        f"$item replace entity @e[type=armor_stand,tag={pet}_new,limit=1] "
        f"armor.head with minecraft:player_head[minecraft:profile={{id:$(uuid)}}]",
    ])

    fn("dismiss", [
        f"function {ns}:{p}/select",
        f"kill @e[type=armor_stand,tag={mine}]",
        f"kill @e[type=marker,tag={mine}]",
        f"scoreboard players reset @s {obj}",
    ])

    fn("here", [
        f"function {ns}:{p}/select",
        f"execute if entity @e[type=armor_stand,tag={mine}] run "
        f"tp @e[type=armor_stand,tag={mine}] @s",
        f"kill @e[type=marker,tag={mine}]",
        f"execute unless entity @e[type=armor_stand,tag={mine}] run "
        f"function {ns}:{p}/none",
    ])
    fn("none", [
        say(f"you have no pet — /function {ns}:{p}/get.", "red"),
    ])

    # --- which entities are mine -------------------------------------------
    #
    # A selector cannot compare a score against another entity's score, so
    # ownership is resolved by marking: copy the player's id to a holder, then
    # tag every pet and crumb whose id matches it. Everything downstream selects
    # on that tag. This runs as the player.
    fn("select", [
        # 0 first, and only overwritten if this player actually has a pet.
        # Without that, a player with no pet leaves `#owner` holding whatever
        # the last player put there — and `dismiss` kills *their* pet.
        f"scoreboard players set #owner {obj} 0",
        f"execute if score @s {obj} matches 1.. run "
        f"scoreboard players operation #owner {obj} = @s {obj}",
        f"tag @a remove {boss}",
        f"tag @s add {boss}",
        f"tag @e[type=armor_stand,tag={pet}] remove {mine}",
        f"tag @e[type=marker,tag={crumb}] remove {mine}",
        f"execute as @e[type=armor_stand,tag={pet}] "
        f"if score @s {obj} = #owner {obj} run tag @s add {mine}",
        f"execute as @e[type=marker,tag={crumb}] "
        f"if score @s {obj} = #owner {obj} run tag @s add {mine}",
    ])

    # --- every tick ---------------------------------------------------------
    fn("tick", [
        f"execute as @a[scores={{{obj}=1..}}] at @s run "
        f"function {ns}:{p}/owner",
    ])

    fn("owner", [
        f"function {ns}:{p}/select",
        # no pet any more (killed, or the chunk it was in went away): stop
        f"execute unless entity @e[type=armor_stand,tag={mine}] run "
        f"function {ns}:{p}/lost",
        f"execute unless entity @e[type=armor_stand,tag={mine}] run return fail",
        # a crumb, but only once you have moved away from the last one. Without
        # the distance test, standing still writes a marker every tick forever.
        f"execute unless entity @e[type=marker,tag={mine},"
        f"distance=..{CRUMB_GAP}] run function {ns}:{p}/crumb",
        f"execute as @e[type=armor_stand,tag={mine},limit=1] at @s run "
        f"function {ns}:{p}/drive",
    ])
    fn("lost", [
        f"scoreboard players reset @s {obj}",
        f"kill @e[type=marker,tag={mine}]",
    ])

    fn("crumb", [
        f"scoreboard players add #seq {seq} 1",
        f'summon minecraft:marker ~ ~ ~ {{Tags:["{crumb}","{crumb}_new"]}}',
        f"execute as @e[type=marker,tag={crumb}_new] run "
        f"function {ns}:{p}/crumb_id",
        f"tag @e[type=marker,tag={crumb}_new] remove {crumb}_new",
        # Cap the trail, oldest first, so what is dropped is history the pet has
        # already walked past rather than the road ahead of it.
        #
        # `if entity @e[...,limit=65]` is not a count — it is true the moment
        # *one* entity matches, so that spelling trimmed the trail on every
        # crumb and the pet had no path to follow. `store result ... if entity`
        # is the count.
        f"execute store result score #crumbs {seq} if entity "
        f"@e[type=marker,tag={mine}]",
        f"execute if score #crumbs {seq} matches {TRAIL + 1}.. run "
        f"function {ns}:{p}/trim",
    ])
    fn("crumb_id", [
        f"scoreboard players operation @s {obj} = #owner {obj}",
        f"scoreboard players operation @s {seq} = #seq {seq}",
        f"tag @s add {mine}",
    ])
    fn("trim", [
        f"function {ns}:{p}/oldest",
        f"kill @e[type=marker,tag={target}]",
    ])

    # --- the walk -----------------------------------------------------------
    #
    # Runs as the pet, at the pet.
    fn("drive", [
        # too far behind to have a hope of catching up on foot: the player
        # teleported, or went up a lift. Come to them and start a fresh trail.
        # the owner, by tag — `mine` is on the pet and the crumbs, never on a
        # player, so testing for it here matched nothing and fired the catch-up
        # every single tick
        f"execute unless entity @a[tag={boss},distance=..{LOST}] run "
        f"function {ns}:{p}/catch_up",
        # Walk only if there is trail left to walk. When you stand still no new
        # crumbs are laid, so the pet eats the last of them and settles beside
        # you — which is the moment it most needs to still be biting, so the
        # fight is not behind this test. An early return here left the pet
        # inert exactly when something walked up to you.
        f"execute if entity @e[type=marker,tag={mine}] run "
        f"function {ns}:{p}/oldest",
        f"execute if entity @e[type=marker,tag={target}] run "
        f"function {ns}:{p}/walk",
        f"function {ns}:{p}/fight",
    ])

    # The oldest crumb is the next place to walk to. There is no "minimum" in a
    # selector, so it is one pass: start high, take the smallest, then tag the
    # crumb holding it.
    fn("oldest", [
        f"tag @e[type=marker,tag={target}] remove {target}",
        f"scoreboard players set #min {seq} 2147483647",
        f"execute as @e[type=marker,tag={mine}] run "
        f"function {ns}:{p}/min_of",
        f"execute as @e[type=marker,tag={mine}] "
        # exactly one crumb can hold the minimum: the counter only ever goes up,
        # so no two crumbs share a sequence number
        f"if score @s {seq} = #min {seq} run tag @s add {target}",
    ])
    fn("min_of", [
        f"execute if score @s {seq} < #min {seq} run "
        f"scoreboard players operation #min {seq} = @s {seq}",
    ])

    fn("walk", [
        # arrived: eat the crumb, and next tick aims at the one after it
        f"execute if entity @e[type=marker,tag={target},"
        f"distance=..{CRUMB_HIT}] run kill @e[type=marker,tag={target}]",
        f"execute if entity @e[type=marker,tag={target}] run "
        f"function {ns}:{p}/step",
    ])
    # `facing ... feet` then a local step is the whole of the movement: it walks
    # along the line to the crumb without any trigonometry, and the vertical
    # part of that line is what carries it up a slope. `tp` is right here in a
    # way it never was for the player — this is an entity, and the client
    # interpolates entity positions between the updates it is sent.
    fn("step", [
        f"execute facing entity @e[type=marker,tag={target},limit=1] feet "
        f"positioned ^ ^ ^{SPEED} if block ~ ~ ~ #{ns}:passable "
        f"run tp @s ^ ^ ^{SPEED}",
        # blocked at foot level by a single step: rise over it
        f"execute facing entity @e[type=marker,tag={target},limit=1] feet "
        f"positioned ^ ^ ^{SPEED} unless block ~ ~ ~ #{ns}:passable "
        f"if block ~ ~1 ~ #{ns}:passable run tp @s ^ ^0.55 ^{SPEED}",
    ])

    fn("catch_up", [
        f"kill @e[type=marker,tag={mine}]",
        f"tp @s @p",
        f"particle minecraft:poof ~ ~0.2 ~ 0.2 0.2 0.2 0.01 6 normal @a",
    ])

    # --- the bite -----------------------------------------------------------
    fn("fight", [
        f"execute if score @s {cool} matches 1.. run "
        f"scoreboard players remove @s {cool} 1",
        f"execute if score @s {cool} matches ..0 "
        f"if entity @e[type=#{ns}:prey,distance=..{BITE_REACH}] run "
        f"function {ns}:{p}/bite",
    ])
    # `by @s from @p`: the pet deals it, the player *causes* it, which is what
    # sends the drops and the experience to the person the pet belongs to. The
    # pet is the only thing near enough for `@p` to be anyone else.
    fn("bite", [
        f"damage @e[type=#{ns}:prey,distance=..{BITE_REACH},limit=1,"
        f"sort=nearest] {BITE_DAMAGE} minecraft:mob_attack by @s from @p",
        f"execute facing entity @e[type=#{ns}:prey,distance=..{BITE_REACH},"
        f"limit=1,sort=nearest] feet positioned ^ ^0.3 ^0.6 run "
        f"particle minecraft:crit ~ ~ ~ 0.1 0.1 0.1 0.05 6 normal @a",
        f"playsound minecraft:entity.wolf.growl neutral @a ~ ~ ~ 0.5 1.6",
        f"scoreboard players set @s {cool} {BITE_COOL}",
    ])

    for name, lines in out.items():
        path = os.path.join(fdir, f"{name}.mcfunction")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    tdir = os.path.join(outdir, "data", ns, "tags", "entity_type")
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, "prey.json"), "w") as f:
        json.dump(_prey(), f, indent=1)

    return {"functions": sorted(out), "prey": len(PREY_CERTAIN) +
            len(PREY_OPTIONAL), "speed": SPEED, "trail": TRAIL,
            "reach": BITE_REACH}


#: What to put in the shared help, so the pet is discoverable beside everything
#: else rather than only in a README.
HELP = [
    (f"{PREFIX}/get", "a pet head that follows your path and bites things"),
    (f"{PREFIX}/here", "call it to you if it is stuck behind something"),
    (f"{PREFIX}/dismiss", "put it away"),
]
