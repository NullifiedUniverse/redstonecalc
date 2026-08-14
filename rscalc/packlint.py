"""Read a datapack back off disk and complain about it.

Everything else in this project is checked by running it. A datapack cannot be:
there is no Minecraft in the test rig, so the commands are the one artefact
here that nothing executes before a player does. That is exactly the situation
that produced the two worst bugs so far — a pack that named its own functions
wrongly, and one that force-loaded nothing — both of which look fine in a diff
and fail silently in a world.

So this reads the pack the way the game's loader would, and checks the things
the game checks at load time or fails on at run time:

* every `function` and `schedule function` names a file that exists;
* a function containing macro lines is only ever called `with` arguments, and
  every `$(placeholder)` it uses is written into that storage first;
* scoreboard objectives are created before anything reads them;
* entity tags in selectors are ones something actually applies;
* braces, brackets and quotes balance, which is what a malformed `tellraw`
  looks like from the outside.

None of that proves the machine computes. It proves the pack loads and the
commands refer to things that exist, which is the failure mode that costs an
afternoon to find in-game and a second to find here.
"""

from __future__ import annotations

import json
import os
import re

#: Arguments that are a **fixed vocabulary**, and the words allowed in each.
#:
#: These matter more than they look. A wrong enum value is not a run-time
#: failure that misbehaves once — the command does not *parse*, and a function
#: containing one command that does not parse **fails to load in its entirety**.
#: The symptom is that `/function <ns>:<name>` answers "Unknown function" while
#: every other function in the same pack works perfectly, which reads like a
#: missing file rather than a typo on line 20 of a file that is right there.
#:
#: This list exists because `bossbar set … color aqua` shipped. A boss bar takes
#: one of seven colours; `aqua` is a *text* colour, from the vocabulary two
#: lines further down the same function. Nothing here objected, and
#: `rscalc:build` — the one command the README tells a player to type — did not
#: exist in game. See DESIGN §38.
ENUMS = {
    # `bossbar set <id> color <colour>`
    ("bossbar", "color"): {"blue", "green", "pink", "purple", "red", "white",
                           "yellow"},
    # `bossbar set <id> style <style>`
    ("bossbar", "style"): {"progress", "notched_6", "notched_10", "notched_12",
                           "notched_20"},
    # `playsound <sound> <source> …`
    ("playsound", None): {"master", "music", "record", "weather", "block",
                          "hostile", "neutral", "player", "ambient", "voice"},
}

#: Text-component colours, which are a *different* vocabulary from the boss
#: bar's and overlap it only partly. Named here so the two cannot be confused
#: again without something noticing.
TEXT_COLOURS = {
    "black", "dark_blue", "dark_green", "dark_aqua", "dark_red",
    "dark_purple", "gold", "gray", "dark_gray", "blue", "green", "aqua",
    "red", "light_purple", "yellow", "white", "reset",
}

#: Commands this project actually emits. A typo in a command name is accepted
#: by no parser here but by the game's, which reports it once per execution and
#: then carries on — 2,000 times a tick, in a pack this size.
KNOWN = {
    "advancement", "attribute", "bossbar", "clear", "damage", "data",
    "difficulty", "effect",
    "execute", "fill", "forceload", "function", "gamemode", "gamerule", "give",
    "item", "kill", "particle", "playsound", "return", "ride", "say",
    "schedule", "scoreboard", "setblock", "stopsound", "summon", "tag", "team",
    "teleport", "tellraw", "time", "title", "tp", "weather", "worldborder",
}


def _functions(root):
    """{'ns:name': path} for every function file in the pack."""
    out = {}
    data = os.path.join(root, "data")
    if not os.path.isdir(data):
        return out
    for ns in sorted(os.listdir(data)):
        fdir = os.path.join(data, ns, "function")
        if not os.path.isdir(fdir):
            continue
        for dirpath, _, names in os.walk(fdir):
            for n in sorted(names):
                if not n.endswith(".mcfunction"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, n), fdir)
                out[f"{ns}:{rel[:-11].replace(os.sep, '/')}"] = \
                    os.path.join(dirpath, n)
    return out


def _enum_problems(body):
    """Fixed-vocabulary arguments whose value is not in the vocabulary.

    Only the run of the command *outside* any quoted string is examined, so a
    boss bar named "…: loading chunks" cannot be mistaken for an argument.

    A macro line (`$…`) may hold `$(x)` where a word belongs; those are skipped
    rather than guessed at, since the value is not known until it runs.
    """
    out = []
    # everything after `run` is a fresh command; check each piece
    for part in re.split(r"(?:^|\s)run\s", body):
        words, quoted = [], False
        for tok in re.findall(r'"[^"]*"|\S+', part):
            if tok.startswith('"'):
                quoted = True
                continue
            words.append(tok)
        del quoted
        if not words:
            continue
        head = words[0]
        for (cmd, key), allowed in ENUMS.items():
            if head != cmd:
                continue
            if key is None:
                # positional: `playsound <sound> <source>`
                if len(words) >= 3 and "$(" not in words[2] \
                        and words[2] not in allowed:
                    out.append(f"{cmd} source {words[2]!r} is not one of "
                               f"{', '.join(sorted(allowed))}")
                continue
            for i, w in enumerate(words[:-1]):
                if w != key:
                    continue
                v = words[i + 1]
                if "$(" in v or v in allowed:
                    continue
                extra = ""
                if cmd == "bossbar" and key == "color" and v in TEXT_COLOURS:
                    extra = (" — that is a *text* colour, not a boss bar one; "
                             "the two vocabularies are different")
                out.append(f"{cmd} {key} {v!r} is not one of "
                           f"{', '.join(sorted(allowed))}{extra}")
    return out


def _balanced(line):
    """Do the brackets and quotes close? A broken component is a silent no-op."""
    depth = {"{": 0, "[": 0, "(": 0}
    close = {"}": "{", "]": "[", ")": "("}
    quoted = esc = False
    for ch in line:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if ch in depth:
            depth[ch] += 1
        elif ch in close:
            depth[close[ch]] -= 1
            if depth[close[ch]] < 0:
                return False
    return not quoted and not any(depth.values())


#: Constructs this pack uses that did not always exist, and the version each
#: one arrived in. A datapack has no way to say "I need at least X" — the format
#: number in `pack.mcmeta` is a compatibility *claim*, not a requirement, and
#: the game will happily load a pack and then fail to parse half of it.
#:
#: The failure is the one §38 traced twice: a command the version does not know
#: is a **parse** error, the whole function is rejected at load, and the player
#: sees "Unknown function" for something that is plainly in the zip. Knowing
#: which construct sets the floor, and where it is used, is the difference
#: between "it does not work" and "move/gear needs 1.20.5, the rest does not".
NEEDS = [
    (re.compile(r"\bgive @\S+ \S+\["), "1.20.5", "item components on /give"),
    (re.compile(r"\bwith minecraft:\S+\["), "1.20.5",
     "item components on /item replace"),
    (re.compile(r"\breturn (?:fail|run)\b"), "1.20.3", "return fail / return run"),
    (re.compile(r"^\$|\bwith storage\b"), "1.20.2", "function macros"),
    (re.compile(r"\{(?:front|back)_text:"), "1.20", "the 1.20 sign text format"),
    (re.compile(r"\bexecute\b.*\bif loaded\b"), "1.19.4", "execute if loaded"),
    (re.compile(r"\bexecute\b.*\bon vehicle\b"), "1.19.4", "execute on vehicle"),
    (re.compile(r"^\s*ride\s"), "1.19.4", "the /ride command"),
    (re.compile(r"^\s*damage\s"), "1.19.4", "the /damage command"),
    (re.compile(r"\bsummon (?:minecraft:)?marker\b"), "1.17", "marker entities"),
]


def _ver_key(v):
    return tuple(int(p) for p in v.split("."))


def requires(root):
    """What the *newest* thing in this pack is, and which files need it.

    Returns ``(version, {version: [(what, where), ...]})``. Nothing here can run
    Minecraft, so this is the closest available thing to knowing whether a pack
    will work on a given world: a list of the constructs it leans on and when
    each one arrived.
    """
    found = {}
    for fid, path in sorted(_functions(root).items()):
        for i, line in enumerate(open(path), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            for pat, ver, what in NEEDS:
                if pat.search(s):
                    found.setdefault(ver, {}).setdefault(what, f"{fid}:{i}")
    if not found:
        return "1.13", {}
    floor = max(found, key=_ver_key)
    return floor, {v: sorted(d.items()) for v, d in found.items()}


def lint(root):
    """Return a list of problems with the pack at `root`. Empty means clean."""
    bad = []
    meta_path = os.path.join(root, "pack.mcmeta")
    if not os.path.exists(meta_path):
        bad.append("no pack.mcmeta — the game will not see this as a pack")
    else:
        try:
            meta = json.load(open(meta_path))["pack"]
        except Exception as e:
            bad.append(f"pack.mcmeta is not readable: {e}")
            meta = {}
        if not ({"min_format", "max_format"} <= set(meta)
                or "pack_format" in meta):
            bad.append("pack.mcmeta declares no format at all")
        # An integer, not a pair. The pair form is what the newer format table
        # is written in, but nothing here can run a client to find out whether
        # a given build parses it, and the cost of guessing wrong is not an
        # error message — it is a pack that does not appear in the world at all,
        # with every `/function` in it reported as an unknown command.
        for k in ("min_format", "max_format", "pack_format"):
            v = meta.get(k)
            if v is not None and not isinstance(v, int):
                bad.append(f"pack.mcmeta {k} must be a plain integer, got "
                           f"{v!r} — a shape the game cannot parse makes the "
                           f"whole pack invisible rather than an error")
        sup = meta.get("supported_formats")
        if sup is not None and not (
                isinstance(sup, dict)
                and isinstance(sup.get("min_inclusive"), int)
                and isinstance(sup.get("max_inclusive"), int)
                and sup["min_inclusive"] <= sup["max_inclusive"]):
            bad.append(f"pack.mcmeta supported_formats must be "
                       f"{{min_inclusive, max_inclusive}}, got {sup!r}")

    funcs = _functions(root)
    if not funcs:
        bad.append("the pack contains no functions")

    macro_fns, called_with, objectives, tags_made = set(), {}, set(), set()
    storage_written = {}

    # first pass: what exists, and what declares what
    for fid, path in funcs.items():
        with open(path) as f:
            body = f.read()
        for line in body.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("$"):
                macro_fns.add(fid)
            for m in re.finditer(r"scoreboard objectives add (\S+)", s):
                objectives.add(m.group(1))
            for m in re.finditer(r'Tags:\[([^\]]*)\]', s):
                tags_made.update(re.findall(r'"([^"]+)"', m.group(1)))
            for m in re.finditer(r"\btag @\S+ add (\S+)", s):
                tags_made.add(m.group(1))
            for m in re.finditer(r"store result storage (\S+) (\S+)", s):
                storage_written.setdefault(m.group(1), set()).add(m.group(2))
            for m in re.finditer(r"data modify storage (\S+) (\S+)", s):
                storage_written.setdefault(m.group(1), set()).add(m.group(2))

    # second pass: does everything referenced exist?
    for fid, path in funcs.items():
        for n, line in enumerate(open(path), 1):
            s = line.strip()
            where = f"{fid}:{n}"
            if not s or s.startswith("#"):
                continue
            body = s[1:].strip() if s.startswith("$") else s
            if not _balanced(body):
                bad.append(f"{where}: unbalanced brackets or quotes: {body[:60]}")
            head = body.split(None, 1)[0] if body.split() else ""
            if head and head not in KNOWN:
                bad.append(f"{where}: unknown command {head!r}")
            for problem in _enum_problems(body):
                bad.append(f"{where}: {problem}")
            for m in re.finditer(r"(?:^|\s)function ([a-z0-9_.-]+:[a-z0-9_./-]+)",
                                 body):
                ref = m.group(1)
                if ref not in funcs:
                    bad.append(f"{where}: calls {ref}, which does not exist")
                tail = body[m.end():]
                mm = re.match(r"\s+with storage (\S+)", tail)
                if mm:
                    called_with.setdefault(ref, set()).add(mm.group(1))
                elif re.match(r"\s+with ", tail):
                    called_with.setdefault(ref, set()).add("?")
            for m in re.finditer(r"schedule function ([a-z0-9_.-]+:[a-z0-9_./-]+)",
                                 body):
                if m.group(1) not in funcs:
                    bad.append(f"{where}: schedules {m.group(1)}, "
                               f"which does not exist")
            for m in re.finditer(r"schedule clear ([a-z0-9_.-]+:[a-z0-9_./-]+)",
                                 body):
                if m.group(1) not in funcs:
                    bad.append(f"{where}: clears a schedule for "
                               f"{m.group(1)}, which does not exist")
            for m in re.finditer(r"(?:if|unless) score (\S+) (\S+)", body):
                if m.group(2) not in objectives:
                    bad.append(f"{where}: reads objective {m.group(2)!r}, "
                               f"which nothing creates")
            for m in re.finditer(r"scoreboard players \w+ (\S+) (\S+)", body):
                if m.group(2) not in objectives:
                    bad.append(f"{where}: uses objective {m.group(2)!r}, "
                               f"which nothing creates")
            # `scores={obj=1..}` in a selector reads the objective too, and
            # against one that does not exist it is an error every tick from
            # world load — the tick tag never stops calling it
            for m in re.finditer(r"scores=\{([^}]*)\}", body):
                for entry in m.group(1).split(","):
                    if "=" not in entry:
                        continue
                    name = entry.split("=")[0].strip()
                    if name and name not in objectives:
                        bad.append(f"{where}: selects on objective {name!r}, "
                                   f"which nothing creates")
            for m in re.finditer(r"#([a-z0-9_.-]+:[a-z0-9_./-]+)", body):
                ref = m.group(1)
                if ref.startswith("minecraft:"):
                    continue          # vanilla tags are the game's to define
                ns, _, path = ref.partition(":")
                found = any(os.path.exists(os.path.join(
                    root, "data", ns, "tags", kind, f"{path}.json"))
                    for kind in ("block", "item", "entity_type", "function",
                                 "fluid", "game_event"))
                if not found:
                    bad.append(f"{where}: uses tag #{ref}, which this pack "
                               f"does not define")
            for m in re.finditer(r"tag=([A-Za-z0-9_.+-]+)", body):
                if m.group(1) not in tags_made:
                    bad.append(f"{where}: selects tag {m.group(1)!r}, "
                               f"which nothing applies")

    tagdir = os.path.join(root, "data", "minecraft", "tags", "function")
    if os.path.isdir(tagdir):
        for n in sorted(os.listdir(tagdir)):
            try:
                vals = json.load(open(os.path.join(tagdir, n)))["values"]
            except Exception as e:
                bad.append(f"minecraft/tags/function/{n}: unreadable: {e}")
                continue
            for v in vals:
                fid = v["id"] if isinstance(v, dict) else v
                if fid.lstrip("#") not in funcs:
                    bad.append(f"minecraft/tags/function/{n} hooks {fid}, "
                               f"which does not exist")

    # macro discipline: a macro function called without arguments is a run-time
    # error every single time, and the game reports it once and keeps going
    for fid in sorted(macro_fns):
        if fid not in called_with:
            bad.append(f"{fid} uses macros but nothing calls it `with` "
                       f"arguments")
    for fid, sources in sorted(called_with.items()):
        if fid not in macro_fns:
            continue
        needs = set()
        for line in open(funcs[fid]):
            if line.startswith("$"):
                needs.update(re.findall(r"\$\(([^)]+)\)", line))
        for src in sources:
            if src == "?":
                continue
            have = storage_written.get(src, set())
            missing = needs - have
            if missing:
                bad.append(f"{fid}: needs {sorted(missing)} from {src}, "
                           f"which only ever gets {sorted(have) or 'nothing'}")
    return bad
