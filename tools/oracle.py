"""Ask the game itself whether a datapack loads.

    python3 tools/oracle.py pack out/build/rscalc_mk3_10bit/datapack
    python3 tools/oracle.py compare  --pack out/build/rscalc_mk3_10bit/datapack
    python3 tools/oracle.py golden   --pack out/build/rscalc_mk3_10bit/datapack

    common:  --version 26.2   --jdk /path/to/jdk-25   --cache ~/.cache/rscalc-mc

Every other check in this repository is a re-implementation. `packlint` is one,
`rscalc/mccheck.py` is another — a good one, measured below, but still this
project's own reading of the game's grammar. This is the game's. It boots the
real registries, builds the real Brigadier dispatcher and hands each function
file to `CommandFunction.fromLines`, which is the call the server makes when a
datapack loads, and for macro functions `instantiate`, which is where `$` lines
are substituted and parsed. "Does this function load" is answered by the code
that will answer it in a world.

It never starts a server and never reads `eula.txt`: it is the same class the
data generator lives beside, run against a directory of functions. No world is
created, nothing listens on a port.

Three commands:

  pack     load every function in a pack; exit 1 if the game rejects any
  compare  mutate the pack's own commands thousands of ways, ask the game about
           each, ask `mccheck` about each, and report where they differ
  golden   the same corpus, sampled, written to tests/data/ as the game's
           recorded verdicts — which is what lets the offline tests check
           `mccheck` against the game without a JDK

What it needs: a JDK 25 (Adoptium's is fine; `--jdk` points at its home) and
the server jar for the version, which is downloaded once and checked against the
sha1 Mojang publishes. 26.1 and later only: those jars ship unobfuscated, which
is what makes calling into them possible at all. For earlier versions the
distilled grammar in `vendor/mc/` is all there is.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import random
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import mc_reports                                    # noqa: E402
from rscalc import mccheck                           # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "data")

#: What each macro parameter really receives. The game validates component
#: values at parse time, so `{id:$(uuid)}` fed a bare 1 is "Malformed", while the
#: int array it is really given is not. Numbers are enough for the rest.
MACROS = {"uuid": "[I;1,2,3,4]", "pitch": "1.2", "mx": "0.1", "my": "0.1",
          "mz": "0.1"}
MACRO_SNBT = "{uuid:[I;1,2,3,4],pitch:1.2d,mx:0.1d,my:0.1d,mz:0.1d}"


def _setup(args):
    jdk = args.jdk or os.environ.get("JDK25")
    if not jdk or not os.path.exists(os.path.join(jdk, "bin", "javac")):
        raise SystemExit(
            "the oracle needs a JDK 25 (it compiles a small class against the "
            "game). Get one from https://adoptium.net and pass --jdk <home>, "
            "or set JDK25.")
    cache = os.path.expanduser(args.cache)
    work = os.path.join(cache, args.version)
    os.makedirs(work, exist_ok=True)
    manifest = json.loads(mc_reports._get(mc_reports.MANIFEST))
    jar, major = mc_reports.download_server(args.version, manifest, work)
    if major < 25:
        raise SystemExit(f"{args.version} needs Java {major}; the oracle "
                         f"targets the unobfuscated 26.x jars (Java 25)")
    global _CWD
    _CWD = work
    java = os.path.join(jdk, "bin", "java")
    mc_reports.unpack_libraries(jar, java, work)
    libs = []
    for dp, _, files in os.walk(os.path.join(work, "libraries")):
        libs += [os.path.join(dp, f) for f in files if f.endswith(".jar")]
    game = os.path.join(work, "versions", args.version,
                        f"server-{args.version}.jar")
    cp = os.pathsep.join([game] + sorted(libs))
    out = os.path.join(work, "oracle-classes")
    os.makedirs(out, exist_ok=True)
    src = os.path.join(HERE, "oracle", "Oracle.java")
    r = subprocess.run([os.path.join(jdk, "bin", "javac"), "-cp", cp, "-d", out,
                        src], capture_output=True, text=True)
    if r.returncode:
        errs = [l for l in r.stderr.splitlines() if "error:" in l][:8]
        raise SystemExit(
            f"the oracle harness no longer compiles against {args.version} — "
            f"the game's internal API moved, and tools/oracle/Oracle.java "
            f"needs updating:\n  " + "\n  ".join(errs))
    return java, cp + os.pathsep + out


_CWD = None


def _run(java, cp, *argv):
    # run inside the cache directory: the game's logger writes `logs/` into its
    # working directory, and that should not be the repository
    r = subprocess.run([java, "-cp", cp, "Oracle", *argv], capture_output=True,
                       text=True, timeout=1800, cwd=_CWD)
    return [l for l in r.stdout.splitlines()
            if l.startswith(("FAIL ", "SUMMARY ")) or re.match(r"\d+\t", l)]


def cmd_pack(args):
    java, cp = _setup(args)
    lines = _run(java, cp, args.pack, MACRO_SNBT)
    bad = [l for l in lines if l.startswith("FAIL ")]
    summary = next((l for l in lines if l.startswith("SUMMARY")), "SUMMARY ? ? ?")
    _, total, macros, failed = summary.split()
    print(f"{args.version}: the game loaded {int(total) - int(failed)} of "
          f"{total} functions ({macros} with macros)")
    for l in bad[:20]:
        print("  " + l[:200])
    return 1 if bad else 0


# --- the corpus --------------------------------------------------------------

JUNK_A = ["zzz", "aqua", "1.5", "-1", "@e", "@s", "~", "^", "{}", "[]", "#a:b",
          "minecraft:stone", "true", "0", "1", "x", "Foo"]
JUNK_B = ["@a[limit=1]", "@p[distance=..3]", "~1", "^1", "1..", "..5", "1..2",
          "minecraft:zombie", "dummy", '"x"', '{text:"x"}', "[I;1]", "!x", "*",
          "#minecraft:logs", "minecraft:oak_planks[]", "armor.chest", "eyes",
          "xz", "20t", "1d", "0.5", "-0", "@n", "@r[type=player]", "minecraft:",
          ":x"]
CHARS = list("abz09_-.:~^@#[]{}=,!*\"' ")


def pack_commands(root):
    """Every distinct command in a pack, with plain numbers zeroed to dedupe."""
    seen, out = set(), []
    base = os.path.join(root, "data")
    for ns in sorted(os.listdir(base)):
        fdir = os.path.join(base, ns, "function")
        for dp, _, files in os.walk(fdir):
            for f in sorted(files):
                with open(os.path.join(dp, f), encoding="utf-8") as fh:
                    for line in fh:
                        s = line.strip()
                        if s and not s.startswith("#"):
                            k = mccheck._shape(s)
                            if k not in seen:
                                seen.add(k)
                                out.append(s)
    return out


def corpus(base, seed=262):
    """The pack's commands, plus a few tens of thousands of ways to break them.

    Three families, on purpose: swap a token for junk (which finds wrong
    vocabularies), truncate / swap / insert (which finds wrong shapes), and
    edit single characters (which finds parsers that only look at the start of
    a token). The third family is what exposed a crash in the checker that the
    first two missed.
    """
    rng = random.Random(seed)
    lines = list(base)
    for line in base:
        toks = line.split(" ")
        for i in range(len(toks)):
            for j in rng.sample(JUNK_A, 6) + rng.sample(JUNK_B, 4):
                t = toks[:]
                t[i] = j
                lines.append(" ".join(t))
            lines.append(" ".join(toks[:i] + toks[i + 1:]))          # delete
            lines.append(" ".join(toks[:i + 1] + toks[i:]))          # duplicate
            lines.append(" ".join(toks[:i]))                          # truncate
            if i + 1 < len(toks):
                t = toks[:]
                t[i], t[i + 1] = t[i + 1], t[i]
                lines.append(" ".join(t))                             # swap
        for _ in range(14):
            i = rng.randrange(len(line))
            k = rng.choice("dri")
            c = rng.choice(CHARS)
            lines.append(line[:i] + line[i + 1:] if k == "d" else
                         line[:i] + c + line[i + 1:] if k == "r" else
                         line[:i] + c + line[i:])
    return list(dict.fromkeys(x for x in lines if x.strip()))


def _verdicts(java, cp, lines):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        path = f.name
    try:
        rows = _run(java, cp, "--lines", path, MACRO_SNBT)
    finally:
        os.unlink(path)
    game = {}
    for r in rows:
        i, _, rest = r.partition("\t")
        game[int(i)] = rest.startswith("OK")
    if len(game) != len(lines):
        raise SystemExit(f"the game answered {len(game)} of {len(lines)} lines")
    return [game[i] for i in range(len(lines))]


def cmd_compare(args):
    java, cp = _setup(args)
    lines = corpus(pack_commands(args.pack), args.seed)
    theirs = _verdicts(java, cp, lines)
    g = mccheck.load(args.version)
    g.macros = MACROS
    fa, fr = [], []
    for l, ok in zip(lines, theirs):
        mine = g.check(l) is None
        if mine and not ok:
            fa.append(l)
        elif ok and not mine:
            fr.append(l)
    n = len(lines)
    print(f"{args.version}: {n:,} commands "
          f"({sum(theirs):,} the game accepts, {n - sum(theirs):,} it refuses)")
    print(f"  agree {n - len(fa) - len(fr):,} "
          f"({100 * (n - len(fa) - len(fr)) / n:.2f}%)")
    print(f"  mccheck accepts what the game refuses: {len(fa)}")
    print(f"  mccheck refuses what the game accepts: {len(fr)}"
          f" ({sum('  ' in l for l in fr)} of them stray double spaces, which "
          f"the game tolerates and the pack never writes)")
    for l in fa[:10]:
        print("   FALSE ACCEPT  " + l[:120])
    for l in [x for x in fr if "  " not in x][:10]:
        print("   FALSE REFUSE  " + l[:120] + "\n       mine: "
              + (g.check(l) or "")[:100])
    return 1 if fa else 0


def cmd_golden(args):
    """Record the game's verdicts on a sample, for the offline tests."""
    java, cp = _setup(args)
    base = pack_commands(args.pack)
    rng = random.Random(1)
    lines = corpus(base, args.seed)
    lines = [l for l in lines if "  " not in l]     # tolerated, never emitted
    keep = list(base)
    rest = [l for l in lines if l not in set(keep)]
    rng.shuffle(rest)
    keep += rest[: args.size - len(keep)]
    verdicts = _verdicts(java, cp, keep)
    os.makedirs(GOLDEN, exist_ok=True)
    path = os.path.join(GOLDEN, f"oracle_{args.version}.json.gz")
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump({"version": args.version, "macros": MACROS,
                   "cases": [[l, int(v)] for l, v in zip(keep, verdicts)]},
                  f, separators=(",", ":"))
    ok = sum(verdicts)
    print(f"{path}: {len(keep):,} commands, {ok:,} accepted and "
          f"{len(keep) - ok:,} refused by the game, "
          f"{os.path.getsize(path) // 1024} KB")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["pack", "compare", "golden"])
    ap.add_argument("pack_dir", nargs="?")
    ap.add_argument("--pack", help="a built datapack directory")
    ap.add_argument("--version", default="26.2")
    ap.add_argument("--jdk")
    ap.add_argument("--cache", default="~/.cache/rscalc-mc")
    ap.add_argument("--seed", type=int, default=262)
    ap.add_argument("--size", type=int, default=12000,
                    help="golden: how many commands to record")
    args = ap.parse_args()
    args.pack = args.pack or args.pack_dir
    if not args.pack:
        ap.error("give a pack directory")
    args.pack = os.path.abspath(args.pack)
    return {"pack": cmd_pack, "compare": cmd_compare, "golden": cmd_golden}[
        args.command](args)


if __name__ == "__main__":
    sys.exit(main())
