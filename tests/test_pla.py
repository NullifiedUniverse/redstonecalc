"""Verify that compiled redstone matches the logical netlist it came from."""

import sys, os, itertools, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine
from rscalc.netlist import Netlist
from rscalc.pla import compile_netlist


def run_cases(nl, cases, label, max_report=3):
    world = World()
    L = compile_netlist(nl, world)
    problems = world.lint()
    assert not problems, f"{label} lint: {problems[:5]}"
    e = Engine(world)
    e.run_until_stable()
    bad = 0
    for case in cases:
        for name, pos in L.levers.items():
            e.set_lever(pos, bool(case.get(name, False)))
        e.run_until_stable()
        ref = nl.evaluate(case)
        for oname, node in nl.outputs.items():
            want = ref[node.idx]
            got = e.high(L.outputs[oname])
            if got != want:
                bad += 1
                if bad <= max_report:
                    print(f"    {label} {case}: {oname} got {got} want {want}")
    assert bad == 0, f"{label}: {bad} mismatches"
    return world, L, e


def test_xor():
    nl = Netlist()
    a, b = nl.input("A"), nl.input("B")
    nl.output("Y", nl.xor(a, b))
    cases = [{"A": x, "B": y} for x, y in itertools.product([0, 1], repeat=2)]
    w, L, e = run_cases(nl, cases, "XOR")
    print(f"  XOR: 4/4 cases, depth {nl.depth()}, {len(w.blocks)} blocks: OK")


def test_all_two_input_functions():
    """Every one of the 16 two-input Boolean functions, as a 2-level SOP."""
    for mask in range(16):
        nl = Netlist()
        a, b = nl.input("A"), nl.input("B")
        terms = []
        for i in range(4):
            if mask >> i & 1:
                av, bv = bool(i & 1), bool(i & 2)
                # NOT(minterm) = OR of the complemented literals
                terms.append(nl.gate([(a, av), (b, bv)]))
        if not terms:
            continue
        nl.output("Y", nl.gate([(t, True) for t in terms]))
        cases = [{"A": x, "B": y} for x, y in itertools.product([0, 1], repeat=2)]
        run_cases(nl, cases, f"fn{mask:04b}")
    print("  all 16 two-input functions: OK")


def full_adder_netlist():
    nl = Netlist()
    a, b, c = nl.input("A"), nl.input("B"), nl.input("C")
    s = nl.xor(nl.xor(a, b), c)
    # carry = majority(a,b,c) as a two-level SOP
    t1 = nl.gate([(a, True), (b, True)])
    t2 = nl.gate([(a, True), (c, True)])
    t3 = nl.gate([(b, True), (c, True)])
    cout = nl.gate([(t1, True), (t2, True), (t3, True)])
    nl.output("S", s)
    nl.output("COUT", cout)
    return nl


def test_full_adder():
    nl = full_adder_netlist()
    cases = [{"A": a, "B": b, "C": c}
             for a, b, c in itertools.product([0, 1], repeat=3)]
    w, L, e = run_cases(nl, cases, "FA")
    # cross-check against arithmetic
    for a, b, c in itertools.product([0, 1], repeat=3):
        for nm, v in (("A", a), ("B", b), ("C", c)):
            e.set_lever(L.levers[nm], bool(v))
        e.run_until_stable()
        tot = a + b + c
        assert e.high(L.outputs["S"]) == bool(tot & 1)
        assert e.high(L.outputs["COUT"]) == bool(tot >> 1)
    print(f"  full adder: 8/8 cases arithmetic-checked, "
          f"depth {nl.depth()}, {len(w.blocks)} blocks: OK")


def test_4bit_ripple_adder():
    nl = Netlist()
    A = [nl.input(f"A{i}") for i in range(4)]
    B = [nl.input(f"B{i}") for i in range(4)]
    carry = None
    for i in range(4):
        if carry is None:
            s = nl.xor(A[i], B[i])
            carry = nl.and_(A[i], B[i])
        else:
            s = nl.xor(nl.xor(A[i], B[i]), carry)
            t1 = nl.gate([(A[i], True), (B[i], True)])
            t2 = nl.gate([(A[i], True), (carry, True)])
            t3 = nl.gate([(B[i], True), (carry, True)])
            carry = nl.gate([(t1, True), (t2, True), (t3, True)])
        nl.output(f"S{i}", s)
    nl.output("COUT", carry)

    cases = []
    for a in range(16):
        for b in range(16):
            c = {}
            for i in range(4):
                c[f"A{i}"] = (a >> i) & 1
                c[f"B{i}"] = (b >> i) & 1
            c["_a"], c["_b"] = a, b
            cases.append(c)
    w, L, e = run_cases(nl, cases, "ADD4")

    # arithmetic check on the real circuit
    for a in range(16):
        for b in range(16):
            for i in range(4):
                e.set_lever(L.levers[f"A{i}"], bool((a >> i) & 1))
                e.set_lever(L.levers[f"B{i}"], bool((b >> i) & 1))
            e.run_until_stable()
            got = sum((1 << i) for i in range(4) if e.high(L.outputs[f"S{i}"]))
            got |= (16 if e.high(L.outputs["COUT"]) else 0)
            assert got == a + b, f"{a}+{b} = {got}"
    print(f"  4-bit ripple adder: all 256 operand pairs correct, "
          f"depth {nl.depth()}, {len(w.blocks)} blocks: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} place-and-route tests\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as ex:
            print(f"  FAIL {t.__name__}: {ex}"); failed += 1
        except Exception as ex:
            import traceback; traceback.print_exc()
            print(f"  ERROR {t.__name__}: {type(ex).__name__}: {ex}"); failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
