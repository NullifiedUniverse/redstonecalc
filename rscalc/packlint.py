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

#: Commands this project actually emits. A typo in a command name is accepted
#: by no parser here but by the game's, which reports it once per execution and
#: then carries on — 2,000 times a tick, in a pack this size.
KNOWN = {
    "advancement", "attribute", "clear", "data", "difficulty", "effect",
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
        for k in ("min_format", "max_format"):
            v = meta.get(k)
            if v is not None and not (isinstance(v, list) and len(v) == 2
                                      and all(isinstance(i, int) for i in v)):
                bad.append(f"pack.mcmeta {k} must be [major, minor], got {v!r}")

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
