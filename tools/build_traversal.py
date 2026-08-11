"""Write the traversal datapack: a grappling hook, a dash and a waypoint.

    python3 tools/build_traversal.py            # out/build/rscalc_move/
    python3 tools/build_traversal.py --zip
    python3 tools/build_traversal.py --mc 1.21

It is a separate pack from the machine on purpose — it has nothing to do with
redstone and it is useful in any world. Drop both in and they do not interact.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc import mcbuild, packlint, traverse

OUT = os.path.join(os.path.dirname(__file__), "..", "out", "build")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mc", default=mcbuild.MC_VERSION_DEFAULT,
                    choices=sorted(mcbuild.MC_FORMATS),
                    help=f"target game version (default "
                         f"{mcbuild.MC_VERSION_DEFAULT})")
    ap.add_argument("--zip", action="store_true",
                    help="also write the .zip that drops into datapacks/")
    args = ap.parse_args()

    root = os.path.abspath(os.path.join(OUT, traverse.NS))
    os.makedirs(root, exist_ok=True)
    pack = os.path.join(root, "datapack")
    info = traverse.build(pack, mc_version=args.mc)

    # The pack is never executed here, so this is the only thing standing
    # between a typo and a player finding it. Refuse to ship a broken one.
    problems = packlint.lint(pack)
    if problems:
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        raise SystemExit(f"refusing to write a pack with {len(problems)} "
                         f"problems")

    zipped = None
    if args.zip:
        import zipfile
        zipped = os.path.join(root, f"{traverse.NS}_datapack.zip")
        with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED, 6) as z:
            for dp, _, fs in os.walk(pack):
                for f in sorted(fs):
                    full = os.path.join(dp, f)
                    z.write(full, os.path.relpath(full, pack))

    print(f"{traverse.NS}: {len(info['functions'])} functions, "
          f"{info['files']} files, lint clean")
    print(f"  pull {info['pull']} blocks/tick, dash {info['dash']} blocks, "
          f"reach {info['reach']}")
    print(f"  targets Minecraft {args.mc}")
    if zipped:
        print(f"  zip: {os.path.relpath(zipped)} "
              f"({os.path.getsize(zipped)/1024:.0f} KB)")
    print(f"  in {root}")


if __name__ == "__main__":
    main()
