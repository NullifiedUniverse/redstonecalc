"""The calculator: an N-bit redstone ALU.

Opcode (3 bits, OP2..OP0)::

    000 ADD   R = A + B
    001 SUB   R = A - B          (A + ~B + 1)
    010 AND   R = A & B
    011 OR    R = A | B
    100 XOR   R = A ^ B
    101 NOT   R = ~A
    110 SHL   R = A << 1
    111 SHR   R = A >> 1

Flags: CARRY, ZERO, NEG (result MSB), OVF (signed overflow).

Two adders are provided so the optimisation can be *measured* rather than
asserted:

  ``ripple``  carry chain of C[i+1] = G[i] + P[i]·C[i], two stages per bit
  ``cla``     carry-lookahead in 4-bit blocks; every carry inside a block is
              one flat sum-of-products, so a block costs two stages no matter
              how wide it is

The lookahead expansion substitutes ``G[j] = A[j]·Badj[j]`` directly into each
product term instead of computing G as its own gate. G would otherwise cost a
stage of its own, and folding it in keeps every product term two stages deep.
"""

from __future__ import annotations

from .netlist import Netlist
from .logic import decode_onehot

OPS = ["ADD", "SUB", "AND", "OR", "XOR", "NOT", "SHL", "SHR"]
CLA_BLOCK = 4


def build_alu(width=8, carry="cla"):
    """The ALU on its own, with its own inputs and outputs — the test target."""
    nl = Netlist()
    A = [nl.input(f"A{i}") for i in range(width)]
    B = [nl.input(f"B{i}") for i in range(width)]
    OP = [nl.input(f"OP{i}") for i in range(3)]
    out = alu_core(nl, A, B, OP, carry)
    for i, r in enumerate(out["R"]):
        nl.output(f"R{i}", r)
    for f in ("CARRY", "OVF", "NEG", "ZERO"):
        nl.output(f, out[f])
    return nl


def alu_core(nl, A, B, OP, carry="cla"):
    """The ALU as a subcircuit: bring your own netlist, operands and opcode.

    Returns ``{"R": [...], "CARRY": n, "OVF": n, "NEG": n, "ZERO": n}`` without
    declaring any outputs, so a larger machine can carry on building from the
    result — into a decimal converter and a display, for instance.
    """
    width = len(A)
    assert len(B) == width and len(OP) == 3
    ZERO_IN = nl.zero()

    # --- opcode decode ------------------------------------------------------
    # nm[k] = NOT(op == k): one gate, since it is just an OR of literals
    nm = {k: decode_onehot(nl, OP, k, name=f"nm_{OPS[k]}") for k in range(8)}

    sub_bar = nm[1]                      # NOT(op == SUB)

    # --- operand conditioning ----------------------------------------------
    # Badj = B XOR sub. sub = NOT(sub_bar), so B XOR sub == XNOR(B, sub_bar),
    # which saves the stage that materialising `sub` would have cost.
    Badj = [nl.xnor(B[i], sub_bar, name=f"Badj{i}") for i in range(width)]
    cin = nl.not_(sub_bar, name="CIN")   # carry-in is 1 for subtract

    # --- adder front end ----------------------------------------------------
    # P = A + Badj (propagate), nG = NOT(A * Badj)
    P, nG, X = [], [], []
    for i in range(width):
        p = nl.gate([(A[i], False), (Badj[i], False)], name=f"P{i}")
        ng = nl.gate([(A[i], True), (Badj[i], True)], name=f"nG{i}")
        # A XOR Badj == P AND nG
        x = nl.not_(nl.gate([(p, True), (ng, True)]), name=f"X{i}")
        P.append(p); nG.append(ng); X.append(x)

    # --- carry network ------------------------------------------------------
    if carry == "ripple":
        C = _ripple_carries(nl, width, A, Badj, P, cin)
    elif carry == "cla":
        C = _cla_carries(nl, width, A, Badj, P, cin)
    else:
        raise ValueError(carry)

    add_bits = [nl.xor(X[i], C[i], name=f"SUM{i}") for i in range(width)]

    # --- logic unit ---------------------------------------------------------
    and_bits = [nl.and_(A[i], B[i]) for i in range(width)]
    or_bits = [nl.or_(A[i], B[i]) for i in range(width)]
    xor_bits = [nl.xor(A[i], B[i]) for i in range(width)]
    not_bits = [nl.not_(A[i]) for i in range(width)]
    shl_bits = [ZERO_IN if i == 0 else A[i - 1] for i in range(width)]
    shr_bits = [ZERO_IN if i == width - 1 else A[i + 1] for i in range(width)]

    per_op = {
        0: add_bits, 1: add_bits, 2: and_bits, 3: or_bits,
        4: xor_bits, 5: not_bits, 6: shl_bits, 7: shr_bits,
    }

    # --- output multiplexer -------------------------------------------------
    # R[i] = OR over k of (op==k AND value_k[i]).
    # NOT(op==k AND v) == nm[k] OR NOT(v), one gate; then OR the complements.
    # ADD and SUB both read the adder, so they share one term:
    # NOT(op is ADD or SUB) == OP1 OR OP2
    nm_addsub = nl.gate([(OP[1], False), (OP[2], False)], name="nm_ADDSUB")
    selects = [(nm_addsub, add_bits)] + [(nm[k], per_op[k]) for k in range(2, 8)]

    R = []
    for i in range(width):
        terms = [nl.gate([(sel, False), (vals[i], True)])
                 for sel, vals in selects]
        R.append(nl.gate([(t, True) for t in terms], name=f"R{i}"))

    # --- flags --------------------------------------------------------------
    # CARRY and OVF only mean anything for ADD/SUB, so they are ANDed with the
    # add/sub select and read 0 for the logic operations.
    ovf_raw = nl.xor(C[width], C[width - 1])
    return {
        "R": R,
        "CARRY": nl.not_(nl.gate([(nm_addsub, False), (C[width], True)]),
                         name="CARRY"),
        "OVF": nl.not_(nl.gate([(nm_addsub, False), (ovf_raw, True)]),
                       name="OVF"),
        "NEG": nl.buf(R[width - 1], name="NEG"),
        "ZERO": nl.not_(nl.gate([(r, False) for r in R]), name="ZERO"),
    }


def _ripple_carries(nl, width, A, Badj, P, cin):
    """C[i+1] = G[i] + P[i]*C[i] — two gate stages per bit."""
    C = [cin]
    for i in range(width):
        t1 = nl.gate([(A[i], True), (Badj[i], True)])        # NOT G[i]
        t2 = nl.gate([(P[i], True), (C[i], True)])           # NOT(P[i] * C[i])
        C.append(nl.gate([(t1, True), (t2, True)], name=f"C{i+1}"))
    return C


def _cla_carries(nl, width, A, Badj, P, cin):
    """Carry-lookahead in blocks of CLA_BLOCK bits.

    Inside a block every carry is one flat sum of products:

        C[b+k+1] = G[b+k] + P[b+k]G[b+k-1] + ... + P[b+k]..P[b]*C[b]

    Each product term is a single gate (an OR of complemented literals) and the
    carry is a single gate over those, so the whole block costs two stages
    regardless of its width.
    """
    C = [cin]
    block_cin = cin
    for b in range(0, width, CLA_BLOCK):
        n = min(CLA_BLOCK, width - b)
        for k in range(n):
            terms = []
            for j in range(k, -1, -1):
                # P[b+j+1..b+k] * G[b+j], with G[b+j] = A[b+j] * Badj[b+j]
                lits = [(P[b + m], True) for m in range(j + 1, k + 1)]
                lits += [(A[b + j], True), (Badj[b + j], True)]
                terms.append(nl.gate(lits))
            # the block's own carry-in, gated by every propagate below it
            lits = [(P[b + m], True) for m in range(0, k + 1)]
            lits.append((block_cin, True))
            terms.append(nl.gate(lits))
            C.append(nl.gate([(t, True) for t in terms], name=f"C{b+k+1}"))
        block_cin = C[b + n]
    return C


# --- reference model --------------------------------------------------------

def reference(op, a, b, width):
    """What the ALU should produce, as (result, carry, zero, neg, ovf)."""
    mask = (1 << width) - 1
    sign = 1 << (width - 1)
    carry = 0
    ovf = 0
    if op in (0, 1):
        bb = b if op == 0 else (~b & mask)
        cin = 0 if op == 0 else 1
        total = a + bb + cin
        r = total & mask
        carry = 1 if total > mask else 0
        # signed overflow: operands agree in sign but the result does not
        ovf = 1 if ((a ^ r) & (bb ^ r) & sign) else 0
    elif op == 2:
        r = a & b
    elif op == 3:
        r = a | b
    elif op == 4:
        r = a ^ b
    elif op == 5:
        r = ~a & mask
    elif op == 6:
        r = (a << 1) & mask
    else:
        r = (a >> 1) & mask
    return r, carry, 1 if r == 0 else 0, 1 if r & sign else 0, ovf


def alu_inputs(op, a, b, width):
    """Lever settings for one operation."""
    vals = {}
    for i in range(width):
        vals[f"A{i}"] = (a >> i) & 1
        vals[f"B{i}"] = (b >> i) & 1
    for j in range(3):
        vals[f"OP{j}"] = (op >> j) & 1
    vals["ZERO"] = 0
    return vals
