/* Verify docs/preview.html in a real browser.

   The page has to boot the whole machine, animate it a tick at a time, and get
   the same answers the Python tests get — driven only through the levers and
   the operation buttons, read only off the lamps.

   Run from the repository root: node tools/check_preview.mjs */
import { chromium } from "playwright";
import path from "path";

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
const errs = [];
page.on("pageerror", e => errs.push("pageerror: " + e.message));
page.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text()); });

const t0 = Date.now();
await page.goto("file://" + path.resolve("docs/preview.html"));
// poll on a timer, not on animation frames: drawing half a million
// instances can starve rAF, and the wait would never get a slot
await page.waitForFunction("window.__ready === true", null,
                           { timeout: 300000, polling: 250 });
console.log(`boot: ${((Date.now() - t0) / 1000).toFixed(1)}s`);

const info = await page.evaluate(() => ({
  blocks: circ.n, instances: inst.length, gates: circ.gates,
  depth: circ.depth, delay: circ.delay, ops: circ.ops, width: circ.width,
  digits: circ.digits, flags: circ.flags,
}));
console.log("loaded:", info);

// --- the tick animation itself -------------------------------------------
const anim = await page.evaluate(async () => {
  const before = eng.now;
  document.querySelector("#bA0").click();          // flip one operand bit
  const touched = [];
  for (let i = 0; i < 40; i++) touched.push(stepTick());
  return { advanced: eng.now - before, touchedPerTick: touched,
           active: touched.filter(t => t > 0).length,
           queue: eng.queue.length };
});
console.log(`animation: ${anim.advanced} ticks stepped, ` +
            `${anim.active}/40 of them changed blocks, ` +
            `peak ${Math.max(...anim.touchedPerTick)} blocks in a tick, ` +
            `${anim.queue} events still pending`);
if (anim.advanced !== 40) throw new Error("stepTick did not advance the clock");
if (anim.active === 0) throw new Error("no block changed while stepping ticks");

// --- correctness, driven through the controls -----------------------------
// Levers are moved a couple of game ticks apart, the way a hand moves them.
// Flipping all twenty inside one tick aligns every hazard in the machine at
// the same instant, which is not something a player can do to it.
const run = async (op, a, b) => await page.evaluate(async ([op, a, b]) => {
  for (const [pad, v] of [["A", a], ["B", b]])
    for (let i = 0; i < circ.width; i++)
      if (!!world.lit[switchIdx[pad + i]] !== !!((v >> i) & 1)) {
        toggleBit(pad, i);
        eng.run(2);
      }
  pressButton(String(op));
  const t = eng.now;
  settleNow();
  return { value: readValue(), settle: eng.now - t,
           flags: Object.fromEntries(circ.flags.map(
             f => [f, world.power[flagIdx[f]] > 0 ? 1 : 0])),
           burned: eng.burned.size };
}, [op, a, b]);

const OPS = info.ops;
const ref = (op, a, b, w) => {
  const mask = (1 << w) - 1, sign = 1 << (w - 1);
  let r, carry = 0, ovf = 0;
  if (op === 0 || op === 1) {
    const bb = op === 0 ? b : (~b & mask), cin = op === 0 ? 0 : 1;
    const total = a + bb + cin;
    r = total & mask; carry = total > mask ? 1 : 0;
    ovf = ((a ^ r) & (bb ^ r) & sign) ? 1 : 0;
  } else if (op === 2) r = a & b;
  else if (op === 3) r = a | b;
  else if (op === 4) r = a ^ b;
  else if (op === 5) r = ~a & mask;
  else if (op === 6) r = (a << 1) & mask;
  else r = a >> 1;
  return { r, CARRY: carry, ZERO: r === 0 ? 1 : 0,
           NEG: r & sign ? 1 : 0, OVF: ovf };
};

const cases = [];
for (let op = 0; op < OPS.length; op++)
  for (const [a, b] of [[0, 0], [1023, 1023], [999, 24]])
    cases.push([op, a, b]);
// and a consecutive run with nothing cleared between operations
const consec = [[0, 12, 34], [0, 12, 34], [1, 12, 34], [1, 900, 899],
                [2, 900, 899], [3, 900, 899], [0, 1023, 1], [0, 0, 0]];

let bad = 0, worst = 0;
for (const group of [cases, consec]) {
  for (const [op, a, b] of group) {
    const got = await run(op, a, b);
    const want = ref(op, a, b, info.width || 10);
    worst = Math.max(worst, got.settle);
    const flagsOk = info.flags.every(f => got.flags[f] === want[f]);
    if (got.value !== want.r || !flagsOk) {
      bad++;
      if (bad <= 4)
        console.log(`  ${OPS[op]} ${a},${b} -> ${got.value} want ${want.r}`,
                    got.flags, want);
    }
  }
}
// --- the merged geometry still describes the world it came from -----------
// Structure blocks are drawn as stretched runs rather than one box each, which
// is half the geometry and exactly the kind of optimisation that can be one
// cell wrong and look fine. Walk the blocks and insist each is inside the box
// that claims it.
const geom = await page.evaluate(() => {
  let mismapped = 0, mx = 0, mz = 0, structure = 0;
  for (const L of runs) if (L > mx) mx = L;
  for (const L of runsZ) if (L > mz) mz = L;
  for (let i = 0; i < world.n; i++) {
    const j = instOf[i];
    if (j < 0) continue;
    const r = inst[j];
    if (world.kind[i] !== world.kind[r]) { mismapped++; continue; }
    if (world.kind[i] === K_SOLID) {
      structure++;
      if (world.by[i] !== world.by[r] ||
          world.bx[i] < world.bx[r] || world.bx[i] >= world.bx[r] + runs[j] ||
          world.bz[i] < world.bz[r] || world.bz[i] >= world.bz[r] + runsZ[j])
        mismapped++;
    } else if (i !== r) mismapped++;
  }
  return { blocks: world.n, instances: inst.length, structure,
           longestX: mx, longestZ: mz, mismapped };
});
if (geom.mismapped)
  throw new Error(`${geom.mismapped} blocks fall outside the instance that ` +
                  `claims them — a merged run is the wrong length`);
if (geom.instances >= geom.blocks)
  throw new Error("structure runs did not merge at all");
console.log(`geometry: ${geom.blocks.toLocaleString()} blocks drawn as ` +
            `${geom.instances.toLocaleString()} instances ` +
            `(${geom.structure.toLocaleString()} structure merged into runs up ` +
            `to ${geom.longestX}×${geom.longestZ}), none mismapped`);

const burned = await page.evaluate(() => eng.burned.size);
console.log(`operations: ${cases.length + consec.length - bad}/` +
            `${cases.length + consec.length} correct ` +
            `(${cases.length} single, ${consec.length} consecutive), ` +
            `worst settle ${worst} gt, ${burned} torches burned`);

// --- the interface itself, which is where the last real bug lived ----------
// A user pressed SHL, then ADD, and got SHL's answer under ADD's highlight:
// the page tracked one pending release, so the second press orphaned the
// first lever and left it down. Two keys held is a state the keypad answers
// honestly — both latch and the opcode is their OR — so the page must never
// create it by accident, and must report what the machine holds rather than
// what was clicked.
const ui = await page.evaluate(async () => {
  const setAB = (a, b) => {
    for (const [pad, v] of [["A", a], ["B", b]])
      for (let i = 0; i < circ.width; i++)
        if (!!world.lit[switchIdx[pad + i]] !== !!((v >> i) & 1)) {
          toggleBit(pad, i); eng.run(2);
        }
  };
  const down = () => circ.ops.filter((n, k) => world.lit[buttonIdx[String(k)]]);
  const latched = () => circ.ops.filter(
    (n, k) => world.power[latchIdx[String(k)]] > 0);

  setAB(10, 72);
  pressButton("6"); settleNow();                  // SHL: 10 << 1
  const shl = { value: readValue(), down: down(), latched: latched() };

  pressButton("6"); eng.run(10); pressButton("0");  // ADD before SHL pops out
  settleNow();
  const then = { value: readValue(), down: down(), latched: latched(),
    highlighted: circ.ops.filter((n, k) =>
      document.getElementById("op" + k).getAttribute("aria-pressed") === "true") };
  return { shl, then };
});
if (ui.shl.value !== 20)
  throw new Error(`SHL 10 should be 20, got ${ui.shl.value}`);
if (ui.then.value !== 82)
  throw new Error(`ADD 10,72 after SHL should be 82, got ${ui.then.value}` +
                  ` (levers down: ${ui.then.down})`);
if (ui.then.down.length)
  throw new Error(`levers left held down: ${ui.then.down}`);
if (ui.then.latched.length !== 1 || ui.then.latched[0] !== "ADD")
  throw new Error(`keypad should latch ADD alone, holds ${ui.then.latched}`);
if (ui.then.highlighted.join() !== ui.then.latched.join())
  throw new Error(`page highlights ${ui.then.highlighted} but the machine ` +
                  `holds ${ui.then.latched}`);
console.log(`controls: pressing ADD 10 gt into SHL's hold releases SHL, ` +
            `latches ADD alone, and reads 82`);

// --- strength is rendered, not just coloured ------------------------------
const relief = await page.evaluate(() => {
  const h = new Set();
  let lo = Infinity, hi = -Infinity;
  for (let j = 0; j < inst.length; j++) if (world.kind[inst[j]] === K_WIRE) {
    const v = +scales[j * 3 + 1].toFixed(3);
    h.add(v); lo = Math.min(lo, v); hi = Math.max(hi, v);
  }
  // From the whole-machine view most rays honestly miss — it is a sparse
  // lattice seen from far away, and about one ray in twelve hits anything.
  // Test what a user gets instead: the control wall, filling the frame, and
  // `pickNear`, which forgives an imprecise pointer the way a tap needs.
  focus("ctrl", 0);          // snap: a view change animates now
  let pick = "", tried = 0, hit = 0;
  for (let a = 1; a < 5; a++)
    for (let b = 1; b < 5; b++) {
      tried++;
      const d = describe(pickNear(cv.clientWidth * a / 5, cv.clientHeight * b / 5));
      if (d) { hit++; pick = pick || d; }
    }
  focus("all", 0);
  return { levels: h.size, lo, hi, pick, hitRate: `${hit}/${tried}` };
});
if (relief.levels < 8)
  throw new Error(`dust height should track signal level, saw ${relief.levels}`);
if (!relief.pick)
  throw new Error("nothing picked anywhere over the control wall");
console.log(`strength: ${relief.levels} distinct dust heights, ` +
            `${relief.lo}–${relief.hi} blocks tall; ` +
            `pointer hits ${relief.hitRate}, e.g. "${relief.pick}"`);


// --- the viewport is actually drawing -------------------------------------
const px = await page.evaluate(() => {
  const c = document.getElementById("cv"), g = c.getContext("webgl");
  draw();     // no preserveDrawingBuffer: read back in the same task as the draw
  const buf = new Uint8Array(c.width * c.height * 4);
  g.readPixels(0, 0, c.width, c.height, g.RGBA, g.UNSIGNED_BYTE, buf);
  const seen = new Set();
  for (let i = 0; i < buf.length; i += 4)
    seen.add(buf[i] * 65536 + buf[i + 1] * 256 + buf[i + 2]);
  return { distinctColours: seen.size, w: c.width, h: c.height };
});
console.log("canvas:", px);

const perf = await page.evaluate(() => {
  const t1 = performance.now();
  for (let i = 0; i < 40; i++) stepTick();
  return { msPerSimulatedTick: +((performance.now() - t1) / 40).toFixed(3),
           instances: inst.length };
});
console.log("performance:", perf, "(software GL in this container)");

// click through the page, not through Playwright's actionability checks:
// a busy animation loop starves them and they time out
for (const v of ["look_disp", "look_ctrl", "look_all"]) {
  await page.evaluate(id => document.getElementById(id).click(), v);
  await page.waitForTimeout(250);
}
await page.evaluate(() => {
  const c = document.getElementById("cut");
  c.value = 60; c.dispatchEvent(new Event("input"));
});
await page.waitForTimeout(250);
await page.evaluate(() => {
  const c = document.getElementById("cut");
  c.value = 100; c.dispatchEvent(new Event("input"));
});
await page.evaluate(async () => {
  for (const [pad, v] of [["A", 7], ["B", 5]])
    for (let i = 0; i < circ.width; i++)
      if (!!world.lit[switchIdx[pad + i]] !== !!((v >> i) & 1)) toggleBit(pad, i);
  pressButton("0"); settleNow();
  // the tick loop never stops on its own after a press, and Playwright waits
  // for a stable box before it will photograph one
  setPlaying(false);
  document.getElementById("look_disp").click();
});
await page.waitForTimeout(500);
await page.screenshot({ path: "out/preview_machine.png" });
await page.locator(".rig").screenshot({ path: "out/shot_console.png",
                                        animations: "disabled" });
// and the control wall, which is the thing a player actually touches
await page.evaluate(() => document.getElementById("look_ctrl").click());
await page.waitForTimeout(500);
await page.locator("#view").screenshot({ path: "out/shot_controls.png",
                                         animations: "disabled" });

console.log(errs.length ? "ERRORS:\n" + errs.join("\n") : "no page errors");
const ok = !errs.length && bad === 0 && burned === 0;
console.log(ok ? "PASS" : "FAIL");
await browser.close();
process.exit(ok ? 0 : 1);
