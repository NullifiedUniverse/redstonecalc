"""Read a datapack back off disk and complain about it.

Everything else in this project is checked by running it. A datapack cannot be:
there is no Minecraft in the test rig, so the commands are the one artefact
here that nothing executes before a player does. That is exactly the situation
that produced the worst bugs so far — a pack that named its own functions
wrongly, one that force-loaded nothing, one whose build command did not parse —
all of which look fine in a diff and fail silently in a world.

There are two halves, and they are kept apart on purpose:

**Whether a command parses is the game's to say**, and `rscalc/mccheck.py` asks
it: the Brigadier tree Mojang generates for each version, walked the way the game
walks it. That used to be duplicated here as a hand-written list of command names
and a hand-written table of legal values, which is precisely how `aqua` got past
as a boss-bar colour. `lint` calls `mccheck` for the version the pack targets.

**What the commands refer to is checked here**, because no grammar knows it:

* every `function` and `schedule function` names a file that exists;
* a function containing macro lines is only ever called `with` arguments, and
  every `$(placeholder)` it uses is written into that storage first;
* scoreboard objectives are created before anything reads them;
* entity tags in selectors are ones something actually applies;
* block, entity and function tags the pack uses are ones it ships;
* `pack.mcmeta` declares its formats the way the game requires.

None of that proves the machine computes. It proves the pack loads and the
commands refer to things that exist, which is the failure mode that costs an
afternoon to find in-game and a second to find here.
"""

from __future__ import annotations

import json
import os
import re

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


def mcmeta_problems(meta):
    """What the game's own codec would refuse about a `pack.mcmeta` `pack` section.

    The rules were read off the game's error messages, by handing candidate files
    to its `PackMetadataSection` codec (`tools/oracle.py pack`, DESIGN §40). They
    are not obvious, and two of the shapes this project shipped broke them:

      * every format is a plain integer;
      * a pack that uses `min_format`/`max_format` and reaches down to a format
        <= 81 must carry `supported_formats`, or the codec refuses it outright;
      * one whose range reaches above 81 must carry `min_format` and
        `max_format`, or the codec refuses it outright;
      * so a pack spanning both — 1.20 to 26.x — needs all three.

    The `[major, minor]` pairs this project once wrote, on a hunch about the
    newer format table, fail the second rule: they were never accepted.
    """
    bad = []
    for k in ("min_format", "max_format", "pack_format"):
        v = meta.get(k)
        if v is not None and not isinstance(v, int):
            bad.append(f"pack.mcmeta {k} must be a plain integer, got {v!r}")
    sup = meta.get("supported_formats")
    if sup is not None and not (
            isinstance(sup, dict)
            and isinstance(sup.get("min_inclusive"), int)
            and isinstance(sup.get("max_inclusive"), int)
            and sup["min_inclusive"] <= sup["max_inclusive"]):
        bad.append(f"pack.mcmeta supported_formats must be "
                   f"{{min_inclusive, max_inclusive}}, got {sup!r}")
    ints = [v for v in (meta.get("min_format"), meta.get("max_format"),
                        meta.get("pack_format")) if isinstance(v, int)]
    if isinstance(sup, dict):
        ints += [v for v in (sup.get("min_inclusive"),
                             sup.get("max_inclusive")) if isinstance(v, int)]
    if ints and not bad:
        lo, hi = min(ints), max(ints)
        # (a legacy pack with only `pack_format` is fine at any old format: the
        # game parses it and merely calls it incompatible with a newer version)
        if lo <= 81 and sup is None and ("min_format" in meta
                                         or "max_format" in meta):
            bad.append(f"pack.mcmeta reaches down to format {lo}, which the game "
                       f"only accepts together with supported_formats")
        if hi > 81 and not ("min_format" in meta and "max_format" in meta):
            bad.append(f"pack.mcmeta reaches up to format {hi}, which the game "
                       f"only accepts together with min_format and max_format")
        if (isinstance(meta.get("min_format"), int)
                and isinstance(meta.get("max_format"), int)
                and meta["min_format"] > meta["max_format"]):
            bad.append("pack.mcmeta min_format is above max_format")
    return bad


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
        bad += mcmeta_problems(meta)

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

    # does it parse? That is the game's to say, and mccheck asks it, for the
    # version this pack is for (or the newest one vendored, if that is missing)
    from . import mcbuild, mccheck
    if mccheck.versions():
        target = (mcbuild.MC_VERSION_DEFAULT
                  if mcbuild.MC_VERSION_DEFAULT in mccheck.versions()
                  else mccheck.versions()[-1])
        for fid, n, cmd, why in mccheck.problems(root, target):
            bad.append(f"{fid}:{n}: {why}")
        for rel, ent, why in mccheck.tag_problems(root, target):
            bad.append(f"{rel}: {ent} {why}")

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
