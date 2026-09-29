"""Fetch the game's own grammar, so this project stops checking commands against
its author's memory of them.

    python3 tools/mc_reports.py                       # every version below
    python3 tools/mc_reports.py 26.2 --java25 /path/to/java

Why this exists. Three rounds in a row ended with a player typing a command and
being told "Unknown function", because one command in the file did not *parse*
and the game rejects the whole function when that happens. Every one of those
was invisible here: `packlint` knew which command *names* were real and had
some closed vocabularies written down by hand, which is exactly the memory that
was wrong (`aqua` is a text colour, and I wrote it where a boss-bar colour
belonged).

Mojang ships the answer. The server jar carries a data generator that needs no
EULA and no world, and `--reports` writes `commands.json` — the complete
Brigadier tree, every literal and every typed argument — plus the block state
properties and every registry. This tool runs it for each version that matters
and distils the result into `vendor/mc/<version>.json.gz`, which is committed:
about a hundred kilobytes a version, so the tests never need a network.

What is kept, and what is dropped:

  commands    the whole tree, as generated
  registries  ids only, per registry (the protocol ids are dropped)
  blocks      property names and their legal values (the per-state table, which
              is 6 MB of the 6.8, is dropped)

The distilled file is what `rscalc/mccheck.py` reads.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import io

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "vendor", "mc")
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

#: Which versions to keep grammar for, and why each one is there.
#:
#: 1.20.2  first with function macros — the floor the pack cannot go below
#: 1.20.3  where `return` exists and the pack's claimed floor sits
#: 1.20.5  item components replaced item NBT; a real syntax break
#: 1.21    the version the pack was first written against
#: 1.21.11 the last 1.x
#: 26.2    what the launcher calls 1.26.2, and this project's target
#: 26.3    the newest release, so drift shows up as soon as it ships
VERSIONS = ["1.20.2", "1.20.3", "1.20.5", "1.21", "1.21.11", "26.2", "26.3"]


def _get(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def _java_for(major, java25):
    """The runtime a given server jar needs.

    Java 17 jars run fine on 21, so only 25 needs a separate install.
    """
    if major >= 25:
        if not java25:
            raise SystemExit(
                "26.x servers need Java 25. Pass --java25 /path/to/bin/java "
                "(a JRE from https://adoptium.net is enough).")
        return java25
    return shutil.which("java") or "java"


def version_info(jar):
    """The game's own `version.json`, from inside the bundled server jar.

    It carries the data-pack format the game actually uses, which is the one
    number `pack.mcmeta` has to get right and which was previously typed in from
    a table.
    """
    with zipfile.ZipFile(jar) as z:
        inner = [n for n in z.namelist()
                 if n.startswith("META-INF/versions/") and n.endswith(".jar")]
        if not inner:                                   # 1.17 and earlier
            return json.loads(z.read("version.json"))
        with zipfile.ZipFile(io.BytesIO(z.read(inner[0]))) as zi:
            return json.loads(zi.read("version.json"))


#: Data-driven registries: not in `registries.json` because they are read from
#: the data pack rather than compiled in, but a command argument still names one
#: (`damage <target> 4 minecraft:stone` is refused because there is no such
#: damage type). Only directories that are registries are kept; recipes,
#: advancements, loot tables and structures are content, not vocabulary.
DYNAMIC = ["damage_type", "enchantment", "dimension_type", "banner_pattern",
           "cat_variant", "chat_type", "frog_variant", "instrument",
           "jukebox_song", "painting_variant", "trim_material", "trim_pattern",
           "wolf_variant", "worldgen/biome"]
#: Vanilla tag kinds whose *names* are kept, so `#minecraft:skeletons` in a
#: selector or `#minecraft:logs` in a block predicate can be checked to exist.
TAG_KINDS = ["block", "item", "entity_type", "damage_type", "function",
             "fluid", "game_event", "enchantment"]


def _names(root, sub):
    """Ids (without .json) of every file under ``root/sub``, as vanilla ids."""
    base = os.path.join(root, sub)
    out = []
    for dp, _, files in os.walk(base):
        for f in files:
            if f.endswith(".json"):
                rel = os.path.relpath(os.path.join(dp, f), base)[:-5]
                out.append("minecraft:" + rel.replace(os.sep, "/"))
    return sorted(out)


def distil(reports, server=None):
    """One version's generator output -> the compact form the checker reads."""
    with open(os.path.join(reports, "commands.json")) as f:
        commands = json.load(f)
    with open(os.path.join(reports, "registries.json")) as f:
        regs = json.load(f)
    with open(os.path.join(reports, "blocks.json")) as f:
        blocks = json.load(f)
    registries = {name: sorted(body["entries"]) for name, body in regs.items()}
    tags = {}
    if server:
        mc = os.path.join(server, "data", "minecraft")
        for name in DYNAMIC:
            found = _names(mc, name)
            if found:
                registries.setdefault(f"minecraft:{name}", found)
        for kind in TAG_KINDS:
            for legacy in (kind, kind + "s"):        # `blocks/` before 1.21
                found = _names(mc, os.path.join("tags", legacy))
                if found:
                    tags[kind] = [n.replace("minecraft:", "", 1) for n in found]
                    break
    return {
        "commands": commands,
        "registries": registries,
        "tags": tags,
        "blocks": {name: body.get("properties", {})
                   for name, body in blocks.items()},
    }


def download_server(version, manifest, work):
    """Download and verify ``version``'s server jar into ``work``.

    Returns ``(jar path, required Java major version)``. The jar is code we are
    about to run, so it is checked against the sha1 Mojang publishes for it.
    """
    entry = next((v for v in manifest["versions"] if v["id"] == version), None)
    if entry is None:
        raise SystemExit(f"{version} is not in Mojang's manifest")
    pkg = json.loads(_get(entry["url"]))
    server = pkg["downloads"]["server"]
    major = pkg.get("javaVersion", {}).get("majorVersion", 21)
    jar = os.path.join(work, "server.jar")
    if not os.path.exists(jar):
        data = _get(server["url"])
        if hashlib.sha1(data).hexdigest() != server["sha1"]:
            raise SystemExit(f"{version}: server.jar does not match its "
                             f"published sha1 — refusing to run it")
        with open(jar, "wb") as f:
            f.write(data)
    return jar, major


def unpack_libraries(jar, java, work):
    """Have the bundler extract its `libraries/` and `versions/` next to the jar.

    The data generator does this as a side effect, and the oracle needs the
    resulting class path, so a throwaway generator run is the least fragile way
    to get it.
    """
    if os.path.isdir(os.path.join(work, "libraries")):
        return
    subprocess.run(
        [java, "-DbundlerMainClass=net.minecraft.data.Main", "-jar", jar,
         "--reports", "--output", os.path.join(work, "gen-oracle")],
        check=True, cwd=work, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=600)


def fetch(version, manifest, java25, keep=None):
    work = keep or tempfile.mkdtemp(prefix=f"mc-{version}-")
    jar, major = download_server(version, manifest, work)
    java = _java_for(major, java25)
    gen = os.path.join(work, "gen")
    # `net.minecraft.data.Main` is the data generator, not the server: it never
    # reads eula.txt and never opens a world
    subprocess.run(
        [java, "-DbundlerMainClass=net.minecraft.data.Main", "-jar", jar,
         "--reports", "--server", "--output", gen],
        check=True, cwd=work, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=600)
    out = distil(os.path.join(gen, "reports"), os.path.join(gen))
    out["info"] = version_info(jar)
    return out, major


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("versions", nargs="*", default=VERSIONS)
    ap.add_argument("--java25", default=os.environ.get("JAVA25"),
                    help="path to a Java 25 `java` binary, for 26.x servers")
    ap.add_argument("--cache", help="keep downloaded jars in this directory")
    args = ap.parse_args()

    manifest = json.loads(_get(MANIFEST))
    os.makedirs(OUT, exist_ok=True)
    for v in args.versions:
        keep = os.path.join(args.cache, v) if args.cache else None
        if keep:
            os.makedirs(keep, exist_ok=True)
        data, java = fetch(v, manifest, args.java25, keep)
        data["version"] = v
        path = os.path.join(OUT, f"{v}.json.gz")
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
            json.dump(data, f, separators=(",", ":"), sort_keys=True)
        cmds = len(data["commands"]["children"])
        print(f"{v:8s} java {java}: {cmds} commands, "
              f"{len(data['blocks'])} blocks, "
              f"{len(data['registries'])} registries -> "
              f"{os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    sys.exit(main())
