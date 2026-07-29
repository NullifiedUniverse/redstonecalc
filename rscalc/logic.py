"""Sum-of-products helpers on top of the OR-of-literals primitive.

The hardware gate is an OR of arbitrary literals, so an AND of literals costs
one gate too — it is the OR of their complements, read inverted. That makes any
two-level sum of products exactly two gate stages, however wide it is, which is
the property the whole compiler is built around.

A *term* here is a list of ``(node, want_true)`` pairs meaning the conjunction
of those literals; ``want_true=False`` means the complement.
"""

from __future__ import annotations


def fold_term(nl, term):
    """Drop constants from a term. Returns the term, or 0 / 1 if it collapses.

    A literal reading the constant-0 rail *false* is always satisfied and just
    goes away; reading it *true* makes the whole term impossible. Without this
    a partly-built value — a BCD digit whose upper bits are still the constant,
    say — would ask for one rail through both polarities, which is a constant 1
    that has no single-tap implementation.
    """
    out = []
    for node, want in term:
        c = nl.is_const(node)
        if c is None:
            out.append((node, want))
        elif bool(c) != bool(want):
            return 0                       # literal is false: term is false
    return out or 1                        # nothing left: term is true


def nand_term(nl, term):
    """NOT(AND of the term's literals) — one gate.

    A literal we want *true* contributes its complement to the OR, and one we
    want *false* contributes itself, so the inversion flag is simply
    ``want_true``.
    """
    assert term, "a term needs at least one literal"
    return nl.gate([(node, want) for node, want in term])


def sop(nl, terms, name=None):
    """OR of ANDs — two gate stages regardless of how many terms there are."""
    assert terms, "a sum needs at least one term"
    live = []
    for t in terms:
        f = fold_term(nl, t)
        if f == 1:
            return nl.one()                # one term is always true
        if f != 0:
            live.append(f)
    if not live:
        return nl.zero()
    return nl.gate([(nand_term(nl, t), True) for t in live], name=name)


def all_of(nl, nodes, name=None):
    """AND over nodes, as a two-stage SOP with a single term."""
    return sop(nl, [[(n, True) for n in nodes]], name=name)


def any_of(nl, nodes, name=None):
    """OR over nodes, folding away constants. One stage."""
    live = []
    for n in nodes:
        c = nl.is_const(n)
        if c == 1:
            return nl.one()
        if c is None:
            live.append(n)
    if not live:
        return nl.zero()
    return nl.gate([(n, False) for n in live], name=name)


def decode_onehot(nl, bits, k, name=None):
    """A one-gate NOT(bits == k): an OR of literals, so it costs one stage."""
    return nl.gate([(b, bool((k >> i) & 1)) for i, b in enumerate(bits)],
                   name=name)


def encode_onehot(nl, lines, nbits, name=None):
    """One-hot lines -> a binary word: one OR per output bit, one stage."""
    out = []
    for j in range(nbits):
        members = [lines[k] for k in range(len(lines)) if (k >> j) & 1]
        out.append(nl.or_(*members, name=None if name is None else f"{name}{j}")
                   if members else nl.zero())
    return out
