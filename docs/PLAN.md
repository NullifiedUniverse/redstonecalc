# Mk II — plan for a compact, operable decimal calculator

Revised 2026-07-29 after a research pass and three rounds of measurement.
Figures marked **measured** were simulated on placed blocks in this repository;
figures marked *projected* are estimates and say so.

Live preview: `docs/preview.html`.

---

## 1. What changed in this revision

Three things were assumed last time and are now settled:

- **The interconnect fix landed.** Repeaters now go in as a block-repeater-block
  sandwich, the wiki's standard transmission line. Measured span went from 12
  to 18 blocks per redstone tick, and the 8-bit ALU dropped from **234 to 186
  game ticks** with 5,179 → 3,996 repeaters. Still correct on all 2,048 4-bit
  vectors and 1,120 8-bit vectors.
- **Cross-digit BCD lookahead works**, and cuts the decimal adder from 963
  gates / depth 32 to **542 gates / depth 18** — but does *not* make it faster
  on the clock (386 → 400 gt). Same lesson as carry-lookahead: depth is not the
  constraint.
- **Burnout is now the binding constraint, not wire.** Every large build burns
  torches out at delay-1 repeaters and has to run at delay 2, which doubles
  latency. That is where the next real gain is.

---

## 2. Where the latency goes

`tools/profile_path.py` walks the placed blocks and attributes every game tick
on the critical path.

| build | total | logic | rail wire | collector wire |
|---|---|---|---|---|
| 4-bit CLA | 108 gt | 24 (22%) | 40 (37%) | 44 (41%) |
| **8-bit CLA** | **190 gt** | **28 (15%)** | **88 (46%)** | **74 (39%)** |

Wire was 88% of the critical path before this revision and is 85% after. The
sandwich change bought a real 20%, but the shape of the problem is unchanged:
**compact and fast are the same goal.**

Two layout rules were learned the hard way and are now enforced in the router,
both the same root cause — *dust only powers a block it points at, and dust
only points where it connects*:

- A sandwich's front block may not follow a **tapped** cell. That cell already
  connects sideways to the tap's riser, so it points along its own run and not
  at the block.
- A sandwich's front block may not be a run's **first** cell, because the source
  cell connects downward to the riser that fed it.

Both left the block, and everything past it, silently dead.

---

## 3. Decision: work in decimal (BCD) — **measured**

A binary machine cannot show a decimal answer without a binary→BCD conversion,
and double-dabble costs roughly an add-and-compare per bit per digit. A BCD
datapath deletes that stage and makes the display a flat 4→7 lookup.

| module | gates | depth | blocks | settle | verified |
|---|---|---|---|---|---|
| Seven-segment decoder, 1 digit | 17 | 2 | 2,647 | 24 gt | all 10 digits |
| Seven-segment display, 1 digit | — | — | 168 | 4 gt | all 10 digits |
| 3-digit BCD adder + display, ripple digit carry | 963 | 32 | 94,660 | 386 gt | 14 sums + segments |
| 3-digit BCD adder + display, **digit lookahead** | **542** | **18** | 102,290 | 400 gt | 14 sums + segments |
| *Mk I 8-bit binary ALU (no display at all)* | 723 | 14 | 123,354 | 360 gt | 1,120 vectors |

All at delay-2 repeaters, which every build this size needs (see §5).

A digit carries when `a + b + cin > 9`, so it **generates** when `a + b >= 10`
and **propagates** when `a + b == 9` — both functions of that digit's own
operands, which is exactly what allows lookahead. The correction folds into the
same add: `result = (a+b) + cin + 6·cout`, since `−10 ≡ +6 (mod 16)`.

Lookahead nearly halves gates and depth. It does not help the clock, and is
slightly *larger* in blocks because its flat product terms span more rails and
so lengthen collectors. Take it for the gate count, not the speed.

---

## 4. Decision: bit-sliced tiles — *projected*

Wire length is heavily skewed: **rail length median 2, mean 19.5, p90 58, max
298**. The wires are not uniformly long; a small tail is catastrophic. Long
rails come from high fanout (the opcode selects feed every bit), long collectors
from wide fan-in spread across the rail set.

So the fix is targeted, not global: one tile per digit, cloned along a row,
with rails stopping at the tile boundary and only the carry crossing. Build one
tile, profile it, and only clone it if the rail share has actually collapsed.

---

## 5. Decision: get rid of the torches — **measured mechanism**, *projected gain*

Every large build burns torches out at delay-1 repeaters:

| build | delay 1 | delay 2 |
|---|---|---|
| 8-bit ALU | 186 gt, 1 torch burned, 76/648 wrong | 360 gt, clean |
| 3-digit BCD, ripple | 216 gt, 26 burned, wrong | 386 gt, clean |
| 3-digit BCD, lookahead | 208 gt, 37 burned, wrong | 400 gt, clean |

Minecraft kills a torch forced off more than eight times in 60 game ticks, and
hazard glitching in a deep network does exactly that. Longer repeater delays
spread the transitions out and fix it completely — at roughly double the
latency. That is the single largest cost in the machine right now.

**Comparators have no burnout rule at all**, and one inverts: in subtract mode
with a rear constant, `out = max(rear − side, 0)`. With a rear of 1, any
nonzero side gives 0 and a zero side gives 1 — an exact logical inverter, no
torch, and the same 2 game ticks a torch takes. Verified in the simulator.

The catch: its output is only as strong as the rear constant, so a tap built
this way needs a repeater to restore it — 2 redstone ticks against the torch's
1. That is the trade:

| | now | torch-free *projected* |
|---|---|---|
| 8-bit ALU, safe operation | 360 gt (delay 2) | ~215 gt (delay 1) |
| Burnout possible | yes, mitigated | **no, structurally** |

Roughly **1.7× faster and impossible to burn out**. This is the highest-value
item on the list and should be built first.

---

## 6. Decision: spend more block types

Semantics confirmed against the wiki this revision unless noted.

| block | property used | replaces | status |
|---|---|---|---|
| **Comparator, subtract** | `out = max(rear − side, 0)`, 2 gt, no burnout | Torch inverters — see §5 | **measured** |
| **Copper bulb** | Toggles on a **rising edge**, keeps its state, comparator-readable at 15 when lit, emits light. **Not conductive** | A multi-block latch **and** a display lamp, in one block | confirmed; needs a pulse, not steady power |
| **Repeater locking** | A repeater held from the side freezes its output | Torch latches; zero torches, so no burnout | **measured**, 44 blocks |
| **Observer** | 15-strength pulse, 2 gt long, 2 gt delay, one block | Multi-block pulse shapers — and the pulse source copper bulbs need | confirmed |
| **Lectern** | `signal = floor(1 + (page−1)/(pages−1) × 14)` | The menu selector — turn a page to pick the operation | confirmed |
| **Comparator + barrel** | Reads container fullness as 0–15 | Constants and small lookup tables | to build |
| **Slabs, stairs, glass** | Non-conductive; a top slab still supports dust | Part of the cell pitch that exists only to stop cross-talk | to build |
| **Note block** | Sounds when powered | Nothing — it is there so the keypad answers you | to build |

The copper bulb's rising-edge rule matters for the design: it cannot be driven
by a steady level, so latching the answer needs an observer (or a button) to
produce the edge. That pairs the two blocks naturally.

The locked-repeater latch has a protocol requirement found while testing it and
now covered by `tests/test_display.py`: **the lock must be asserted a tick
before the data changes**, or the data wins the race and the latch stores the
new value instead of holding the old one.

---

## 7. Player control and the menu

- **0–9 keypad** of stone buttons feeding a shift register. Entry is typing a
  number, not flipping nineteen levers and doing binary in your head.
- **Lectern as the menu** — a comparator reads the open page, so turning to
  page 2 selects SUB. The menu is a physical book you can label.
- **Note-block feedback** — one pitch on keypress, another when the answer
  latches, so it can be operated without watching the display.
- **Clear and enter** — clear resets the entry register, enter latches the
  operand and starts the sum.

---

## 8. What it should come to

| | Mk I, 8-bit binary | Mk II, 3-digit decimal |
|---|---|---|
| Range | 0–255 | 0–999 |
| Blocks | 123,354 **measured** | ~55,000 *projected* |
| Logic depth | 14 **measured** | 18 **measured**, with digit lookahead |
| Settle, safe operation | 360 gt **measured** | ~120 gt *projected*, torch-free at delay 1 |
| Wire share of latency | 85% **measured** | ~55% *projected* |
| Shows a decimal answer | no | yes, 17 gates per digit |
| Input | 19 levers, in binary | keypad and a lectern |

The projections rest on the torch-free tap (§5) and the tile change (§4). Those
are the two assumptions to test, in that order.

---

## 9. Build order

Steps 5 and 6 are **done** — see §10. The rest stands.

1. **Torch-free inverting tap** — comparator plus repeater. Biggest single win:
   removes burnout structurally and lets everything run at delay 1.
2. **One bit-sliced digit tile.** Profile it. If rail wire has not collapsed,
   §4 is wrong and stops there.
3. Add copper bulbs and observers to the simulator.
4. Clone the tile three times; wire the digit-lookahead carry chain.
5. ~~Keypad~~ **built** — ten buttons, one-hot locked-repeater latches, no
   torches in the latch cell. Lectern menu and note blocks still to do.
6. ~~Join decoder and display in one world~~ **built** — the loom carries nine
   nets over 27 risers with no crossings, and the two digits sit on different
   feed levels so the units lanes pass beneath the tens digit.
7. Re-run the full vector sweep and the burnout check.

---

## 10. The console, as built — **measured**

One world, keypad to lamps, no software glue anywhere in the path:

```
two 10-key pads -> one-hot latches -> BCD encode -> decimal adder
                -> seven-segment decoders -> wiring loom -> two lamp digits
```

| | |
|---|---|
| Blocks | 36,045 |
| Extent | 224 x 97 x 461 |
| Gates / depth | 191 / 17 |
| Loom | 9 nets, 27 risers, no crossings |
| Repeater delay | 2 — at delay 1 it burns torches out, as every large build does |
| Settle, worst case | **384 gt** (19.2 s) from button to lamps |
| Verified | **100/100** sums, pressed as buttons and read off the lamps, in Python *and* in the browser |

This is the first build that is genuinely *operated* rather than configured: no
lever is set by hand anywhere in the datapath. It also confirms the plan's
central claim from the other direction — the machine is 191 gates and depth 17,
yet still takes 384 game ticks, because the loom and the rails are most of it.
The torch-free tap (§5) remains the highest-value item left.

What is still open from §7: the lectern menu (the machine only adds), note-block
feedback, and widening from one digit to three.

---

## Verification in place

```sh
python3 tests/test_mechanics.py   # 12/12  redstone rules
python3 tests/test_cells.py       #  5/5   NOR cell and gates
python3 tests/test_pla.py         #  4/4   place-and-route
python3 tests/test_alu.py         #  4/4   Mk I ALU, exhaustive at 4 bits
python3 tests/test_display.py     #  2/2   seven-segment display, latch
python3 tests/test_console.py     #  2/2   the whole console, driven by buttons
node   tests/browser_check.js     #        the demo, in a real browser
node   tools/check_preview.mjs    #        the console page: 100 sums in-browser
python3 tools/profile_path.py     #        critical-path breakdown
python3 -m tools.build_preview    #        build and verify the preview bundle
```
