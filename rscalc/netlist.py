"""Logic netlist targeting the redstone PLA primitive.

The hardware primitive is a **collector row**: a dust line fed by any number of
*taps*, each tap taking one rail either through a redstone torch (inverting) or
through a repeater (non-inverting). The row is a wired-OR, so one gate computes

    out = OR over taps of (rail, or its complement)

i.e. an OR of arbitrary literals. That is functionally complete and gives any
two-level sum-of-products in exactly two gate stages, which is the reason this
primitive was chosen: every stage costs exactly one redstone tick.

All taps of a gate must come from the immediately preceding stage, so `gate()`
inserts non-inverting buffers automatically to align its inputs.
"""

from __future__ import annotations


class Node:
    __slots__ = ("nl", "idx", "kind", "stage", "taps", "name")

    def __init__(self, nl, idx, kind, stage, taps, name):
        self.nl = nl
        self.idx = idx
        self.kind = kind          # "input" | "gate"
        self.stage = stage
        self.taps = taps          # [(node_idx, inverted)]
        self.name = name

    def __repr__(self):
        return f"<{self.kind} {self.name or self.idx}@s{self.stage}>"


class Netlist:
    def __init__(self):
        self.nodes: list[Node] = []
        self.inputs: dict[str, Node] = {}
        self.outputs: dict[str, Node] = {}
        self._bufs: dict[tuple[int, int], Node] = {}
        self.const0 = None
        self.const1 = None

    # -- construction --
    def _add(self, kind, stage, taps, name):
        n = Node(self, len(self.nodes), kind, stage, taps, name)
        self.nodes.append(n)
        return n

    def input(self, name):
        n = self._add("input", 0, [], name)
        self.inputs[name] = n
        return n

    def gate(self, taps, name=None):
        """taps: list of (Node, inverted). Result stage = 1 + aligned stage."""
        assert taps, "a gate needs at least one tap"
        # A rail can only be tapped once per gate: the tap occupies one fixed
        # cell beside the collector. Repeating a tap is harmless and is folded
        # away here; tapping one rail through *both* polarities would need two
        # different structures in that single cell, and is a constant 1 anyway.
        seen, merged = {}, []
        for node, inv in taps:
            if node.idx in seen:
                if seen[node.idx] != inv:
                    raise ValueError(
                        f"gate taps {node.name or node.idx} both ways: that is "
                        f"constant 1, and cannot be built as a single tap")
                continue
            seen[node.idx] = inv
            merged.append((node, inv))
        taps = merged
        target = max(t[0].stage for t in taps)
        aligned = [(self.lift(n, target), inv) for n, inv in taps]
        return self._add("gate", target + 1,
                         [(n.idx, inv) for n, inv in aligned], name)

    def lift(self, node, stage):
        """Buffer `node` up to `stage` with non-inverting pass-through gates."""
        assert stage >= node.stage, (node, stage)
        cur = node
        while cur.stage < stage:
            key = (cur.idx, cur.stage + 1)
            nxt = self._bufs.get(key)
            if nxt is None:
                nxt = self._add("gate", cur.stage + 1, [(cur.idx, False)],
                                f"buf({cur.name or cur.idx})")
                self._bufs[key] = nxt
            cur = nxt
        return cur

    def output(self, name, node):
        self.outputs[name] = node
        return node

    def align_outputs(self):
        """Buffer every output up to the deepest one, and return that stage.

        Outputs left on different stages exit at different heights but can land
        on the same Z, and a loom that runs each net along its source Z at one
        shared level would then drive two nets down the same lane. Aligning them
        makes every exit slot distinct by construction — and has the side
        benefit that flags and digits arrive together instead of the flags
        flickering ahead of the answer.
        """
        if not self.outputs:
            return 0
        target = max(n.stage for n in self.outputs.values())
        for name, node in list(self.outputs.items()):
            self.outputs[name] = self.lift(node, target)
        return target

    # -- logic helpers (all built on OR-of-literals) --
    def not_(self, a, name=None):
        return self.gate([(a, True)], name)

    def buf(self, a, name=None):
        return self.gate([(a, False)], name)

    def or_(self, *args, name=None):
        return self.gate([(a, False) for a in args], name)

    def nand(self, *args, name=None):
        return self.gate([(a, True) for a in args], name)

    def and_(self, *args, name=None):
        return self.not_(self.nand(*args), name)

    def nor(self, *args, name=None):
        return self.not_(self.or_(*args), name)

    def xor(self, a, b, name=None):
        t1 = self.gate([(a, True), (b, False)])      # = NOT(a AND NOT b)
        t2 = self.gate([(a, False), (b, True)])      # = NOT(NOT a AND b)
        return self.gate([(t1, True), (t2, True)], name)

    def xnor(self, a, b, name=None):
        t1 = self.gate([(a, True), (b, True)])       # = NOT(a AND b)
        t2 = self.gate([(a, False), (b, False)])     # = NOT(NOT a AND NOT b)
        return self.gate([(t1, True), (t2, True)], name)

    def mux2(self, sel, a, b, name=None):
        """sel ? a : b"""
        t1 = self.gate([(sel, True), (a, True)])     # = NOT(sel AND a)
        t2 = self.gate([(sel, False), (b, True)])    # = NOT(NOT sel AND b)
        return self.gate([(t1, True), (t2, True)], name)

    def zero(self):
        """A constant 0 rail (an input that is never switched on)."""
        if self.const0 is None:
            self.const0 = self.input("ZERO")
        return self.const0

    def one(self):
        """A constant 1: the zero rail, inverted once and shared."""
        if self.const1 is None:
            self.const1 = self.not_(self.zero(), name="ONE")
        return self.const1

    def is_const(self, node):
        """0, 1, or None — used by the sum-of-products builder to fold."""
        if node is self.const0:
            return 0
        if node is self.const1:
            return 1
        return None

    # -- reference evaluation --
    def evaluate(self, values: dict):
        """values: {input name: bool}. Returns a list of node values."""
        v = [False] * len(self.nodes)
        for n in self.nodes:
            if n.kind == "input":
                v[n.idx] = bool(values.get(n.name, False))
            else:
                acc = False
                for src, inv in n.taps:
                    acc = acc or ((not v[src]) if inv else v[src])
                v[n.idx] = acc
        return v

    # -- stats --
    def depth(self):
        return max((n.stage for n in self.nodes), default=0)

    def gate_count(self):
        return sum(1 for n in self.nodes if n.kind == "gate")

    def stage_nodes(self, s):
        return [n for n in self.nodes if n.stage == s]

    def summary(self):
        lines = [f"gates={self.gate_count()} inputs={len(self.inputs)} depth={self.depth()}"]
        for s in range(self.depth() + 1):
            ns = self.stage_nodes(s)
            if ns:
                lines.append(f"  stage {s:2d}: {len(ns)} nodes")
        return "\n".join(lines)
