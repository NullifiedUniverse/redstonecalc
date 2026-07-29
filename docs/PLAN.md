# Mk II — plan for a compact, operable decimal calculator

Mk I works and is verified. This is the plan for the next one. Figures marked
**measured** were simulated on placed blocks in this repository; figures marked
*projected* are estimates and are labelled as such everywhere they appear.

Live preview: `docs/preview.html`.

---

## 1. The finding that sets the plan

`tools/profile_path.py` walks the placed blocks of the compiled ALU and
attributes every game tick on the critical path to a cause.

| build | total | logic | rail wire | collector wire |
|---|---|---|---|---|
| 4-bit ripple | 138 gt | 32 (23%) | 46 (33%) | 60 (43%) |
| 4-bit CLA | 136 gt | 24 (18%) | 54 (40%) | 58 (43%) |
| 8-bit ripple | 250 gt | 48 (19%) | 98 (39%) | 104 (42%) |
| **8-bit CLA** | **240 gt** | **28 (12%)** | **108 (45%)** | **104 (43%)** |

**88% of the machine's latency is repeaters spent keeping dust alive over
distance.** Not gates.

Two consequences:

- On this architecture **compact and fast are the same goal**. Every block of
  wire removed is latency removed. The user's instinct that these were separate
  asks is, on the evidence, wrong in a useful way — they collapse into one.
- The obvious lever is the wrong one. Carry-lookahead bought 1.7× the logical
  depth and returned 1.05× on the clock. More clever arithmetic will not help
  until the wires are shorter.

---

## 2. Decision: work in decimal (BCD), not binary — **measured**

A binary machine cannot display a decimal answer without a binary→BCD
conversion, and double-dabble costs roughly an add-and-compare per bit per
digit. Building the datapath in BCD deletes that stage and makes the display a
flat 4→7 lookup.

Both halves were built and simulated rather than estimated
(`tools/prototype_decimal.py`, `rscalc/display.py`):

| module | gates | depth | blocks | settle | verified |
|---|---|---|---|---|---|
| Seven-segment decoder, 1 digit | 17 | 2 | 2,647 | 24 gt | all 10 digits |
| Seven-segment display, 1 digit | — | — | 168 | 4 gt | all 10 digits |
| 3-digit BCD adder + display decode | 963 | 32 | 94,660 | 256 gt | 11 sums, digits + segments |
| *Mk I 8-bit binary ALU (no display at all)* | 723 | 14 | 123,354 | 234 gt | 1,120 vectors |

A 3-digit decimal adder *including its entire display path* is 23% smaller than
the binary ALU that still cannot show a decimal number.

The catch is depth: 32 stages against 14, because the BCD carry ripples from
digit to digit and each digit is two chained 4-bit adds.

**Fix, and it is the first thing to build.** A digit generates a carry when its
sum exceeds 9, which is a function of its own operands alone — so the same flat
lookahead already used inside a digit applies across digits. Depth 32 → ~14
*projected*.

---

## 3. Decision: bit-sliced tiles, not a machine-wide crossbar — *projected*

Mk I routes every stage as a PLA: rails span the whole machine so any gate can
tap any signal. That generality is exactly what bought the 45% rail cost.

A decimal digit talks almost entirely to itself; only the carry crosses. So:
one tile per digit, cloned along a row, rails stopping at the tile boundary. A
tile is ~30 blocks wide instead of 269 — the difference between ten repeaters
on a rail and one.

This is the assumption most likely to be wrong, so it is tested first: build
one tile, profile it with `tools/profile_path.py`, and only clone it if the
rail share has actually collapsed.

---

## 4. Decision: spend more block types

| block | property used | replaces | status |
|---|---|---|---|
| **Copper bulb** | Toggles on a rising edge, *keeps* its state, comparator-readable, emits light | A multi-block latch **and** a display lamp, in one block | to confirm |
| **Repeater locking** | A repeater held from the side freezes its output | Torch latches — and with zero torches, burnout is impossible | **measured**, 44 blocks |
| **Comparator, subtract** | `out = rear − side` in one redstone tick | Binary compare and subtract trees | **measured** |
| **Comparator + barrel** | Reads container fullness as 0–15 | Hard-wired constants, small lookup tables | to build |
| **Lectern** | A comparator reads the open page number | The menu selector — turn a page to pick the operation | to build |
| **Observer** | 2 gt pulse on a block update, one block | Multi-block pulse shapers and edge detectors | to confirm |
| **Slabs, stairs, glass** | Non-conductive, but a top slab still supports dust | Part of the 4-block cell pitch that exists only to stop cross-talk | to build |
| **Note block** | Sounds when powered | Nothing — it is there so the keypad answers you | to build |

Two honest caveats:

- **Copper-bulb and observer tick timings could not be verified this session**
  (web search was rate-limited), so they are marked *to confirm* rather than
  assumed. Confirm against the wiki before building on them.
- The locked-repeater latch has a real protocol requirement, found while
  testing it and now covered by `tests/test_display.py`: **the lock must be
  asserted a tick before the data changes.** Move both in the same tick and the
  data wins the race, storing the new value instead of holding the old one.

---

## 5. Decision: the display is the memory

A copper bulb holds its own state and is a light source, so the answer register
and the screen stop being two separate structures. Latch the result into the
bulbs and the datapath behind them is free to start the next sum, with no
separate register bank to build, power or route.

The seven-segment module verified here is 168 blocks and settles in 4 game
ticks; swapping lamps for **waxed** bulbs keeps the geometry and adds the memory
for free *projected*. Waxing matters — unwaxed copper oxidises, which changes
the light level emitted but not the redstone behaviour.

Note on the module's shape: the centre bar is boxed in on all four sides by the
other segments, so it cannot be fed from the side. Every segment is therefore
fed from *below* by a two-torch tower, which is non-inverting and strongly
powers the lamp above it; the dust cap then spreads that along the bar. Feeding
all seven the same way keeps the tile symmetric and clonable.

---

## 6. Player control and the menu

- **0–9 keypad** of stone buttons feeding a shift register. Entry is typing a
  number, not flipping nineteen levers and doing binary in your head.
- **Lectern as the menu** — a comparator reads the open page, so turning to
  page 2 selects SUB. The menu is a physical book you can label.
- **Note-block feedback** — one pitch on keypress, another when the answer
  latches, so it can be operated without watching the display.
- **Clear and enter** — clear resets the entry register, enter latches the
  operand and starts the sum.

---

## 7. What it should come to

| | Mk I, 8-bit binary | Mk II, 3-digit decimal |
|---|---|---|
| Range | 0–255 | 0–999 |
| Blocks | 123,354 **measured** | ~55,000 *projected* |
| Logic depth | 14 **measured** | ~14 *projected*, with digit lookahead |
| Settle | 234 gt **measured** | ~70 gt *projected* |
| Wire share of latency | 88% **measured** | ~55% *projected* |
| Shows a decimal answer | no | yes, 17 gates per digit |
| Input | 19 levers, in binary | keypad and a lectern |

---

## 8. Build order

Each step is checkable alone and can fail loudly before the next is worth
starting.

1. Cross-digit carry lookahead. Target: BCD depth 32 → ~14.
2. One bit-sliced digit tile. Profile it. **If rail wire has not collapsed, the
   plan is wrong and stops here.**
3. Add copper bulbs and observers to the simulator, confirming their tick
   behaviour against the wiki first.
4. Clone the tile three times; wire the carry chain.
5. Keypad, lectern menu, note blocks.
6. Join decoder and display in one world — the 21-net corridor: seven feeds per
   digit, each a two-torch riser to its own transport level, digits kept in
   separate tiles so nets never need to cross.
7. Re-run the full vector sweep and the burnout check on the result.

---

## Verification already in place

```sh
python3 tests/test_mechanics.py   # 12/12  redstone rules
python3 tests/test_cells.py       #  5/5   NOR cell and gates
python3 tests/test_pla.py         #  4/4   place-and-route
python3 tests/test_alu.py         #  4/4   Mk I ALU, exhaustive at 4 bits
python3 tests/test_display.py     #  2/2   seven-segment display, latch
python3 tools/profile_path.py     #        critical-path breakdown
python3 -m tools.prototype_decimal  #      BCD adder + decoder measurements
python3 -m tools.build_preview    #        build and verify the preview modules
```
