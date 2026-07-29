"""Binary to binary-coded-decimal, so the machine can show a decimal answer.

The method is combinational double dabble: shift the binary value left, one bit
at a time, into a row of BCD digits, and before each shift add 3 to any digit
that has reached 5 — because a digit at 5 or more would carry out of decimal
range once doubled. Shifting is free here (it is only which node feeds which
position), so the entire cost is the add-3 cells.

An add-3 cell takes a BCD digit and returns ``d >= 5 ? d + 3 : d``. Inputs 10
through 15 never occur, which makes them don't-cares, and using them shrinks
each output to two or three product terms:

    y3 = x3 + x2.x1 + x2.x0
    y2 = x2.!x1.!x0 + x3.!x1.x0
    y1 = !x3.!x2.x1 + x2.x1.x0 + x3.!x0
    y0 = !x3.!x2.x0 + x2.x1.!x0 + x3.!x1.!x0

Eleven product terms and four collectors: fifteen gates and two stages per
cell. The whole converter is verified exhaustively — every value a machine of
this width can produce — in ``tests/test_bcd.py``.
"""

from __future__ import annotations

from .logic import sop

#: sum-of-products for each output bit of the add-3 cell, over (x3, x2, x1, x0)
ADD3_TERMS = {
    3: [[(3, True)], [(2, True), (1, True)], [(2, True), (0, True)]],
    2: [[(2, True), (1, False), (0, False)],
        [(3, True), (1, False), (0, True)]],
    1: [[(3, False), (2, False), (1, True)],
        [(2, True), (1, True), (0, True)],
        [(3, True), (0, False)]],
    0: [[(3, False), (2, False), (0, True)],
        [(2, True), (1, True), (0, False)],
        [(3, True), (1, False), (0, False)]],
}


def add3_cell(nl, digit):
    """digit: four nodes, LSB first. Returns four nodes, LSB first."""
    assert len(digit) == 4
    return [sop(nl, [[(digit[i], want) for i, want in term]
                     for term in ADD3_TERMS[b]])
            for b in range(4)]


def add3_reference(d):
    return d + 3 if d >= 5 else d


def digits_needed(nbits):
    """How many decimal digits a value of `nbits` bits can need."""
    n, top = 1, 9
    while top < (1 << nbits) - 1:
        n += 1
        top = top * 10 + 9
    return n


def bin_to_bcd(nl, bits, ndigits=None):
    """bits: binary value, LSB first. Returns ndigits groups of 4 nodes.

    Groups come back least-significant digit first, each LSB first, so
    ``out[1][3]`` is bit 3 of the tens digit.
    """
    nbits = len(bits)
    ndigits = ndigits or digits_needed(nbits)
    zero = nl.zero()

    # One shift register: the binary value sits in the low bits and the digits
    # ride above it. Everything shifts left together, so after `nbits` shifts
    # the binary has emptied into the digits.
    reg = list(bits) + [zero] * (4 * ndigits)
    cells = 0
    for _ in range(nbits):
        for k in range(ndigits):
            lo = nbits + 4 * k
            grp = reg[lo:lo + 4]
            # a digit still made entirely of the constant cannot reach 5 yet,
            # and correcting it would be dead logic the pruner has to remove
            if all(n is zero for n in grp):
                continue
            reg[lo:lo + 4] = add3_cell(nl, grp)
            cells += 1
        reg = [zero] + reg[:-1]

    bin_to_bcd.cells = cells
    return [reg[nbits + 4 * k: nbits + 4 * k + 4] for k in range(ndigits)]


def bcd_reference(value, ndigits):
    return [(value // (10 ** k)) % 10 for k in range(ndigits)]
