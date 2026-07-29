"""Static timing analysis: where does the ALU's latency actually go?

Walks the placed blocks to attribute every redstone tick on the critical path
to one of three causes — the tap (real logic), repeaters keeping a rail alive
along X, or repeaters keeping a collector alive along Z.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World
from rscalc.alu import build_alu
from rscalc.pla import compile_netlist


def count_repeaters_x(w, y, z, x0, x1):
    lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
    return sum(1 for x in range(lo, hi + 1)
               if (b := w.get((x, y, z))) and b.kind == "repeater")


def count_repeaters_z(w, y, x, z0, z1):
    lo, hi = (z0, z1) if z0 <= z1 else (z1, z0)
    return sum(1 for z in range(lo, hi + 1)
               if (b := w.get((x, y, z))) and b.kind == "repeater")


def analyse(width, carry, repeater_delay=1):
    nl = build_alu(width, carry)
    w = World()
    L = compile_netlist(nl, w, repeater_delay=repeater_delay)
    step = 2 * repeater_delay          # game ticks per repeater

    # arrival[node] = game ticks after the levers move
    arrival = {}
    blame = {}                          # node -> (tap, rail, coll) ticks so far
    for n in nl.nodes:
        if n.kind == "input":
            arrival[n.idx] = 0
            blame[n.idx] = (0, 0, 0)

    for n in nl.nodes:
        if n.kind != "gate":
            continue
        gx, gy, g_railz = L.pos[n.idx]
        coll_y = gy - 2
        rail_y = gy - 4
        z_exit = g_railz - 2
        best = None
        for src, _inv in n.taps:
            sx, _sy, sz = L.pos[src]
            tap_x = gx + 1
            tap_z = sz + 2
            rail_reps = count_repeaters_x(w, rail_y, sz, sx, tap_x)
            coll_reps = count_repeaters_z(w, coll_y, gx, tap_z, z_exit)
            t = arrival[src] + rail_reps * step + 2 + coll_reps * step
            pt, pr, pc = blame[src]
            cand = (t, (pt + 2, pr + rail_reps * step, pc + coll_reps * step))
            if best is None or cand[0] > best[0]:
                best = cand
        arrival[n.idx] = best[0]
        blame[n.idx] = best[1]

    outs = [(name, node) for name, node in nl.outputs.items()]
    worst_name, worst_node = max(outs, key=lambda kv: arrival[kv[1].idx])
    total = arrival[worst_node.idx]
    tap, rail, coll = blame[worst_node.idx]
    return {
        "width": width, "carry": carry, "depth": nl.depth(),
        "gates": nl.gate_count(), "blocks": len(w.blocks),
        "reps": L.stats["repeaters"], "taps": L.stats["taps"],
        "out": worst_name, "total": total,
        "tap": tap, "rail": rail, "coll": coll,
    }


if __name__ == "__main__":
    print("Critical-path breakdown (game ticks), by cause\n")
    print(f"{'build':16} {'total':>6} {'logic':>12} {'rail wire':>13} {'coll wire':>13}")
    for width in (4, 8):
        for carry in ("ripple", "cla"):
            r = analyse(width, carry)
            t, tap, rail, coll = r["total"], r["tap"], r["rail"], r["coll"]
            print(f"{r['width']}-bit {r['carry']:9} {t:>6} "
                  f"{tap:>6} ({100*tap/t:3.0f}%) {rail:>6} ({100*rail/t:3.0f}%) "
                  f"{coll:>6} ({100*coll/t:3.0f}%)   worst output {r['out']}")
    print("\n'logic' is the tap torch/repeater — the only part doing computation.")
    print("Everything else is repeaters spent keeping dust alive over distance.")
