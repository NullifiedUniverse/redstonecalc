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
try {
  await page.waitForFunction("window.__ready === true", null,
                             { timeout: 300000, polling: 250 });
} catch (e) {
  // The whole point of collecting page errors is to explain a boot that never
  // finishes, and this used to throw the timeout with the collected errors
  // still sitting in the array — a diagnostic that discarded its diagnosis.
  console.error("the page never became ready. What it said on the way:");
  console.error(errs.length ? errs.join("\n") : "  (nothing — it is still working)");
  console.error(await page.evaluate(() =>
    "  boot line: " + (document.getElementById("boot")?.textContent || "(gone)")));
  throw e;
}
console.log(`boot: ${((Date.now() - t0) / 1000).toFixed(1)}s`);

const info = await page.evaluate(() => ({
  blocks: circ.n, instances: inst.length, gates: circ.gates,
  depth: circ.depth, delay: circ.delay, ops: circ.ops, width: circ.width,
  digits: circ.digits, flags: circ.flags,
}));
console.log("loaded:", info);

// --- the masthead says what the bundle says --------------------------------
// Five figures under the headline. Three were filled from the bundle and two
// were typed into the markup — a settle of 2,352 and a burned-torch count of 0
// that would have gone on saying 0 while the machine burned. They are all read
// now, which means they can all be *unread*: an element left at its placeholder
// is the failure this catches.
const mast = await page.evaluate(() => {
  const t = id => (document.getElementById(id) || {}).textContent;
  return { hBlocks: t("hBlocks"), hGates: t("hGates"), hDepth: t("hDepth"),
           hSettle: t("hSettle"), hBurn: t("hBurn"), dpTicks: t("dpTicks"),
           want: { hBlocks: circ.n.toLocaleString(), hGates: String(circ.gates),
                   hDepth: String(circ.depth),
                   hSettle: circ.settle.toLocaleString(), hBurn: "0",
                   dpTicks: String(circ.mc.parts) } };
});
for (const k of Object.keys(mast.want))
  if (mast[k] !== mast.want[k])
    throw new Error(`the page's ${k} reads ${JSON.stringify(mast[k])}, the ` +
                    `bundle says ${JSON.stringify(mast.want[k])}`);
console.log(`masthead: all ${Object.keys(mast.want).length} printed figures ` +
            `come from the bundle and match it`);

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

// --- the page can be read, in both themes ---------------------------------
// Colour is a token system with two themes, and the second one is where things
// rot: the seven-segment readout and the chips over the canvas sit on surfaces
// that are dark whichever way the page is set, so a token that flips with the
// theme lands dark-on-dark there. That is how the lit lamps ended up at 3.0
// against their own background in light theme, and how the *selected* view chip
// ended up at 3.6. Both are measured now rather than looked at.
const contrast = await page.evaluate(async () => {
  const L = c => {
    const [r, g, b] = c.match(/[\d.]+/g).map(Number).slice(0, 3).map(v => v / 255)
      .map(v => v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
    return .2126 * r + .7152 * g + .0722 * b;
  };
  const bgOf = el => {
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && !/rgba\(0, 0, 0, 0\)|transparent/.test(c)) return c;
    }
    return "rgb(255,255,255)";
  };
  const sweep = () => {
    const bad = [];
    for (const el of document.querySelectorAll("body *")) {
      const own = [...el.childNodes].filter(n => n.nodeType === 3)
        .map(n => n.textContent.trim()).join("");
      if (!own) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const size = parseFloat(cs.fontSize), weight = +cs.fontWeight || 400;
      const floor = (size >= 24 || (size >= 18.66 && weight >= 700)) ? 3 : 4.5;
      const a = L(cs.color), bg = L(bgOf(el));
      const ratio = (Math.max(a, bg) + .05) / (Math.min(a, bg) + .05);
      if (ratio < floor)
        bad.push(`${(el.className || el.tagName).toString().slice(0, 24)} ` +
                 `${size.toFixed(0)}px ${ratio.toFixed(2)} "${own.slice(0, 24)}"`);
    }
    return [...new Set(bad)];
  };
  // Switching the theme rewrites custom properties on the root, and Chromium
  // will happily hand back a *partially* recalculated style for a descendant in
  // the frames right after: the element's own --dim reads as the new value
  // while its resolved `color` is still the old one. Sweeping into that gives
  // light text measured against a dark background — a 3.16 that exists in no
  // frame anyone sees. Detaching the root forces a full restyle, and then a
  // canary is checked before believing any of it.
  const settle = async () => {
    const root = document.documentElement;
    const was = root.style.display;
    root.style.display = "none"; void root.offsetHeight; root.style.display = was;
    for (let i = 0; i < 20; i++) {
      await new Promise(r => requestAnimationFrame(r));
      const want = getComputedStyle(root).getPropertyValue("--dim").trim();
      const el = document.querySelector(".bit");
      if (!el) return;
      const got = getComputedStyle(el).color;
      const hex = "#" + got.match(/\d+/g).slice(0, 3)
        .map(n => (+n).toString(16).padStart(2, "0")).join("");
      if (hex === want.toLowerCase()) return;
    }
  };
  const out = {};
  for (const t of ["dark", "light"]) {
    document.documentElement.setAttribute("data-theme", t);
    await settle();
    out[t] = sweep();
  }
  document.documentElement.removeAttribute("data-theme");
  // and the structural things that are cheap to get wrong
  const ids = new Map(), dup = [];
  for (const el of document.querySelectorAll("[id]"))
    ids.has(el.id) ? dup.push(el.id) : ids.set(el.id, 1);
  const nameless = [...document.querySelectorAll("button,select,input,summary,a[href]")]
    .filter(el => !(el.textContent || "").trim() && !el.getAttribute("aria-label")
                  && !el.getAttribute("title") && !(el.labels && el.labels.length))
    .map(el => el.id || el.tagName);
  const hs = [...document.querySelectorAll("h1,h2,h3,h4,h5")].map(h => +h.tagName[1]);
  const jumps = hs.map((v, i) => i && v - hs[i - 1] > 1 ? `${hs[i-1]}→${v}` : null)
                  .filter(Boolean);
  return { ...out, dup, nameless, jumps,
           overflow: document.documentElement.scrollWidth -
                     document.documentElement.clientWidth };
});
for (const t of ["dark", "light"])
  if (contrast[t].length)
    throw new Error(`${t} theme has text below its contrast floor: ` +
                    contrast[t].slice(0, 3).join(" | "));
if (contrast.dup.length)
  throw new Error(`duplicate element ids: ${contrast.dup.join(", ")}`);
if (contrast.nameless.length)
  throw new Error(`controls with no accessible name: ${contrast.nameless.join(", ")}`);
if (contrast.jumps.length)
  throw new Error(`heading levels skip: ${contrast.jumps.join(", ")}`);
if (contrast.overflow > 0)
  throw new Error(`the page scrolls sideways by ${contrast.overflow}px`);
console.log("readable: no text below 4.5:1 in either theme, no duplicate ids, " +
            "every control named, headings in order, no sideways scroll");

// --- the wheel belongs to the page ----------------------------------------
// The canvas is most of a screen. Taking the wheel over it meant a reader
// scrolling down the article stopped dead the moment the pointer crossed the
// instrument — the same trap a thumb hits on a phone, wearing a different hat.
const wheel = await page.evaluate(() => {
  document.querySelector("#view").scrollIntoView({ block: "center" });
  const y0 = scrollY, d0 = cam.dist;
  // a plain wheel must not be swallowed
  const plain = cv.dispatchEvent(new WheelEvent("wheel",
    { deltaY: 200, bubbles: true, cancelable: true }));
  // and the modifier — which is also what a trackpad pinch sends — must zoom
  cv.dispatchEvent(new WheelEvent("wheel",
    { deltaY: -200, ctrlKey: true, bubbles: true, cancelable: true }));
  const zoomed = d0 - cam.dist;
  cam.dist = d0; invalidate();      // put the camera back for what comes after
  return { plainAllowed: plain, zoomed: +zoomed.toFixed(4),
           scrolledByZoom: scrollY - y0,
           hinted: !document.querySelector("#nudge").hidden,
           buttons: !!document.querySelector("#zIn") && !!document.querySelector("#zOut") };
});
if (!wheel.plainAllowed)
  throw new Error("a plain wheel over the canvas is still cancelled, so the " +
                  "page cannot scroll past the instrument");
if (wheel.zoomed <= 0)
  throw new Error(`ctrl + wheel changed the zoom by ${wheel.zoomed}`);
if (!wheel.hinted)
  throw new Error("scrolling over the view said nothing about how to zoom");
if (!wheel.buttons)
  throw new Error("zoom is gesture-only again — there are no zoom buttons");
console.log(`wheel: plain scrolls the page, ⌘/ctrl zooms by ` +
            `${wheel.zoomed.toFixed(3)}, and the view says so once`);

// --- the view, from the keyboard -------------------------------------------
// Every other control on this page is a button or a field and has always been
// reachable by tab. The machine was a picture: no way to turn it, zoom it or
// reframe it without a pointing device. Arrows orbit, shift pans, +/− zoom,
// 0 refits — and none of it may steal the arrow keys from the page once focus
// has moved on, which is the way this kind of handler usually goes wrong.
const keys = await page.evaluate(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const press = (k, shift) => cv.dispatchEvent(new KeyboardEvent("keydown",
    { key: k, shiftKey: !!shift, bubbles: true, cancelable: true }));
  document.querySelector("#view").scrollIntoView({ block: "center" });
  await wait(200);
  cv.focus();
  const focused = document.activeElement === cv;
  const ring = getComputedStyle(cv).boxShadow;

  const az0 = cam.az, el0 = cam.el, y0 = scrollY;
  press("ArrowRight"); const turned = cam.az - az0;
  press("ArrowUp");    const tilted = cam.el - el0;
  const scrolled = scrollY - y0;

  const t0 = target.slice();
  press("ArrowLeft", true);
  const panned = target.some((v, i) => Math.abs(v - t0[i]) > 0.5);

  const d0 = cam.dist;
  press("+"); const inward = cam.dist < d0;
  press("-"); press("-"); const outward = cam.dist > d0;

  focus("disp", 0); await wait(60);
  const near = cam.dist; cam.dist = near * 3;
  press("0");
  // the flight is on rAF and this container renders in software, so poll for
  // the arrival rather than guessing a wait long enough to cover a bad frame
  let refit = false;
  for (let i = 0; i < 40 && !refit; i++) {
    await wait(120);
    refit = Math.abs(cam.dist - near) < near * 0.35;
  }

  // And once focus is elsewhere the arrows belong to the page again. Dispatched
  // on the element that actually has focus and allowed to bubble, which is what
  // a browser does — firing it at the canvas directly would test nothing except
  // that the canvas has a listener, and would "fail" on a page that is right.
  const elsewhere = document.querySelector("#bStep");
  elsewhere.focus();
  const az1 = cam.az, y1 = scrollY;
  const passed = elsewhere.dispatchEvent(new KeyboardEvent("keydown",
    { key: "ArrowDown", bubbles: true, cancelable: true }));
  // read the camera before putting the view back: `focus` moves it, and
  // comparing across that call is how this assertion failed on a page that was
  // behaving perfectly well
  const released = passed && scrollY === y1 && cam.az === az1;
  focus("all", 0);
  return { focused, ring, turned: +turned.toFixed(3), tilted: +tilted.toFixed(3),
           scrolled, panned, inward, outward, refit,
           releasedWhenBlurred: released };
});
if (!keys.focused) throw new Error("the canvas cannot be focused at all");
if (keys.ring === "none")
  throw new Error("a focused canvas shows no focus ring");
if (!(keys.turned > 0 && keys.tilted > 0))
  throw new Error(`arrows did not orbit (az ${keys.turned}, el ${keys.tilted})`);
if (keys.scrolled !== 0)
  throw new Error(`orbiting by keyboard scrolled the page ${keys.scrolled}px`);
if (!keys.panned) throw new Error("shift + arrow did not pan");
if (!(keys.inward && keys.outward))
  throw new Error("+ and − did not zoom in and out");
if (!keys.refit) throw new Error("0 did not refit the view it was last given");
if (!keys.releasedWhenBlurred)
  throw new Error("the canvas still eats arrow keys once focus has left it");
console.log(`keyboard: tab to the view, arrows orbit (${keys.turned} rad) ` +
            `without scrolling, shift pans, +/− zoom, 0 refits, and the ` +
            `arrows go back to the page on blur`);

// --- and it says out loud where it has got to ------------------------------
// The readout is seven-segment lamps, which announce nothing, and the number
// beside them is rewritten on every one of the ~2,300 ticks a settle takes,
// which announces too much. The live region speaks from the transitions
// instead: one sentence when a settle starts, one when the answer lands.
const said = await page.evaluate(async () => {
  const el = document.querySelector("#say");
  const live = el && el.getAttribute("aria-live");
  const hidden = el && getComputedStyle(el).display === "none";
  for (let i = 0; i < circ.width; i++)
    if (!!world.lit[switchIdx["A" + i]] !== !!((42 >> i) & 1)) toggleBit("A", i);
  for (let i = 0; i < circ.width; i++)
    if (!!world.lit[switchIdx["B" + i]] !== !!((7 >> i) & 1)) toggleBit("B", i);
  settleNow();
  // Count real mutations, not just equal strings: writing the same sentence
  // again is still a change to a live region and a screen reader may read it.
  // `takeRecords`, not the callback — this whole block is one task, and an
  // observer's callback is a microtask that cannot run until the task ends, so
  // reading a counter it increments gives 0 however many times it was written.
  const obs = new MutationObserver(() => {});
  obs.observe(el, { childList: true, characterData: true, subtree: true });
  obs.takeRecords();
  pressButton("0");
  let ticks = 0;
  for (; ticks < 60 && eng.queue.length; ticks++) { stepTick(); refreshPanel(); }
  const during = el.textContent, saidWhileSettling = obs.takeRecords().length;
  settleNow();
  obs.disconnect();
  return { live, hidden, during, ticks, saidWhileSettling,
           after: el.textContent };
});
if (said.live !== "polite")
  throw new Error(`the answer's live region is aria-live=${said.live}`);
if (said.hidden)
  throw new Error("the live region is display:none, so it is not in the " +
                  "accessibility tree at all");
if (said.during !== "settling")
  throw new Error(`while settling the page announced ${said.during}`);
if (said.saidWhileSettling !== 1)
  throw new Error(`the live region was written ${said.saidWhileSettling} ` +
                  `times over ${said.ticks} ticks of one settle — it should ` +
                  `be exactly once, when the settle starts`);
if (said.after !== "49, stable")
  throw new Error(`after settling 42+7 the page announced ${said.after}`);
console.log(`announced: "${said.during}" — ${said.saidWhileSettling} write ` +
            `over ${said.ticks} ticks of settling — then "${said.after}"`);

// --- the settle meter has to actually sweep ---------------------------------
// It used to estimate progress from how far the event queue had drained from
// its deepest, which sounds reasonable and is not: this engine's queue holds a
// handful of events at a time whatever the machine is doing, so `1 - q/peak`
// has two or three reachable values. Measured over a real 3,670-tick settle it
// showed exactly two — 0% and 50%. A bar with two positions is not a bar.
const meter = await page.evaluate(() => {
  // Start from rest, *then* change the operands and press — in that order.
  // Settling in between is what a probe does, not what a person does, and it
  // pre-computes the answer: pressing an already-latched operation on operands
  // the machine has already absorbed drains in a handful of ticks, and there is
  // no settle left to measure. That is how this check first failed, on a meter
  // that was working.
  settleNow();
  for (let i = 0; i < circ.width; i++) {
    if (!!world.lit[switchIdx["A" + i]] !== !!((999 >> i) & 1)) toggleBit("A", i);
    if (!!world.lit[switchIdx["B" + i]] !== !!((24 >> i) & 1)) toggleBit("B", i);
  }
  pressButton("0");
  const seen = [];
  for (let n = 0; n < 6000 && eng.queue.length; n++) {
    stepTick();
    if (n % 100 === 0) { refreshPanel(); seen.push(railPct.textContent); }
  }
  const foot = document.getElementById("railTick").textContent;
  const monotone = seen.every((v, i) => i === 0 || parseInt(v) >= parseInt(seen[i - 1]));
  settleNow();
  return { readings: new Set(seen).size, samples: seen.length, monotone, foot,
           target: circ.settle };
});
if (!meter.target)
  throw new Error("the bundle carries no settle length, so the meter has " +
                  "nothing to measure against");
if (meter.samples < 8)
  throw new Error(`only ${meter.samples} samples — the settle was too short ` +
                  `to say anything about the meter, so this check proved ` +
                  `nothing rather than finding a fault`);
if (meter.readings < 8)
  throw new Error(`the meter showed only ${meter.readings} distinct values ` +
                  `over ${meter.samples} samples of one settle`);
if (!meter.monotone) throw new Error("the meter went backwards");
if (!/of ~/.test(meter.foot))
  throw new Error(`the meter's foot does not say what it is counting towards: ` +
                  `"${meter.foot}"`);
console.log(`meter: ${meter.readings} distinct readings over one settle, ` +
            `monotone, counting towards ${meter.target.toLocaleString()} ticks`);

// --- a highlight has to last long enough to be seen -------------------------
// The flash windows were counted in game ticks, and what a viewer sees is
// frames. At the default 200 gt/s a six-tick dust flash lived 1.8 frames, and
// at the 50x setting a component flash lived 0.6 of a frame — visible or not
// depending on where the frame boundary fell, independently for every one of
// thousands of blocks. That is the whole structure flickering, and it got worse
// the faster you ran it.
const fade = await page.evaluate(() => {
  const out = { boot: speed, menu: +document.getElementById("speed").value, at: {} };
  const was = speed;
  for (const v of [20, 60, 200, 1000]) {
    speed = v; setFadeWindows();
    out.at[v] = { dust: +(FADE / (v / 60)).toFixed(2),
                  fire: +(FIRE_FADE / (v / 60)).toFixed(2) };
  }
  speed = was; setFadeWindows();
  return out;
});
if (fade.boot !== fade.menu)
  throw new Error(`the page boots at ${fade.boot} gt/s while the speed menu ` +
                  `reads ${fade.menu}`);
for (const [v, w] of Object.entries(fade.at)) {
  if (w.dust < 3) throw new Error(`at ${v} gt/s a dust flash lasts ${w.dust} frames`);
  if (w.fire < 5) throw new Error(`at ${v} gt/s a component flash lasts ${w.fire} frames`);
}
const dusts = Object.values(fade.at).map(w => w.dust);
if (Math.max(...dusts) - Math.min(...dusts) > 1)
  throw new Error(`a flash lasts ${Math.min(...dusts)}-${Math.max(...dusts)} ` +
                  `frames depending on speed — it should not depend on speed`);
console.log(`flash: ${dusts[0]} frames of dust and ` +
            `${Object.values(fade.at)[0].fire} of a switch at every speed from ` +
            `${Object.keys(fade.at)[0]} to ${Object.keys(fade.at).pop()} gt/s`);

// --- the machine, as a datapack the reader can take away --------------------
// The page assembles a Minecraft datapack from the same block data it is
// simulating, using the (kind, meta) -> block state table `rscalc/mcbuild.py`
// ships in the bundle. The claim worth checking is not that a zip comes out —
// it is that replaying the commands rebuilds *this machine*, block for block,
// with every orientation intact.
const pack = await page.evaluate(() => {
  const mc = circ.mc;
  const t0 = performance.now();
  const { files, commands, parts, ns } = datapackFiles();
  const ms = performance.now() - t0;
  const setb = /^setblock ~(-?\d+) ~(-?\d+) ~(-?\d+) (.+) replace$/;
  const fill = /^fill ~(-?\d+) ~(-?\d+) ~(-?\d+) ~(-?\d+) ~(-?\d+) ~(-?\d+) (.+) replace$/;
  // rebuild every position the commands touch, the way the game would, and
  // compare against the machine itself — counting spans on one axis silently
  // undercounts the moment fills can vary on another
  const world_map = new Map(), kinds = new Set();
  let bad = 0, boxes = 0;
  for (const k in files) {
    if (!/\/part\/\d+\.mcfunction$/.test(k)) continue;
    for (const line of files[k].split("\n")) {
      if (!line) continue;
      let m = setb.exec(line);
      if (m) { world_map.set(`${m[1]},${m[2]},${m[3]}`, m[4]); kinds.add(m[4]); continue; }
      m = fill.exec(line);
      if (!m) { bad++; continue; }
      const a = [+m[1], +m[2], +m[3]], c = [+m[4], +m[5], +m[6]];
      const vary = [0,1,2].filter(i => a[i] !== c[i]);
      if (vary.length > 1) { boxes++; continue; }
      kinds.add(m[7]);
      if (!vary.length) { world_map.set(a.join(","), m[7]); continue; }
      const ax = vary[0];
      for (let v = Math.min(a[ax], c[ax]); v <= Math.max(a[ax], c[ax]); v++) {
        const q = a.slice(); q[ax] = v; world_map.set(q.join(","), m[7]);
      }
    }
  }
  // and every one has to be the block the page is simulating
  let mismatched = 0;
  for (let i = 0; i < world.n; i++) {
    const want = mc.palette[mc.map[world.kind[i] + ":" + world.meta[i]]];
    const got = world_map.get(`${world.bx[i]},${world.by[i]},${world.bz[i]}`);
    if (got !== want) mismatched++;
  }
  const placed = world_map.size;

  // The control functions, which are a separate claim from the blocks. The
  // page does not write them any more: the exporter ships their finished text
  // and this copies the bytes, because two implementations of one artifact
  // drifted three ways in a single round — a different namespace, a `clear`
  // missing its message, and no force-loading at all.
  const ctrl = Object.keys(files)
    .filter(k => k.endsWith(".mcfunction") && !/\/part\/\d+\./.test(k))
    // the same transform `want` uses: functions live in subfolders now, so
    // stripping to the last slash would make sys/probe and move/probe collide
    .map(k => k.replace(/^.*\/function\/|\.mcfunction$/g, "")).sort();
  const copied = Object.keys(packFiles).every(k => files[k] === packFiles[k]);
  // and `load` has to actually cover the machine: one `forceload add` reaches
  // 256 chunks, so the footprint is tiled — miss a tile and the far corner is
  // frozen with nothing to see
  const covered = new Set();
  for (const line of (files[`data/${ns}/function/sys/load_tiles.mcfunction`] || "").split("\n")) {
    const m = /^forceload add ~(\d+) ~(\d+) ~(\d+) ~(\d+)$/.exec(line);
    if (!m) continue;
    for (let x = +m[1]; x <= +m[3]; x += 16)
      for (let z = +m[2]; z <= +m[4]; z += 16) covered.add((x >> 4) + "," + (z >> 4));
  }
  const [dx, , dz] = world.dims;
  let uncovered = 0;
  for (let cx = 0; cx < Math.ceil(dx / 16); cx++)
    for (let cz = 0; cz < Math.ceil(dz / 16); cz++)
      if (!covered.has(cx + "," + cz)) uncovered++;

  const enc = new TextEncoder(), zin = {};
  for (const k in files) zin[k] = enc.encode(files[k]);
  const zip = fflate.zipSync(zin, { level: 6 });
  const back = fflate.unzipSync(zip);
  return { commands, parts, ms: Math.round(ms), placed, bad, boxes, mismatched,
           blocks: world.n, ns, ctrl, copied,
           want: Object.keys(packFiles)
                   .filter(k => k.endsWith(".mcfunction"))
                   .map(k => k.replace(/^.*\/function\/|\.mcfunction$/g, ""))
                   .sort(),
           namespace: mc.namespace, uncovered, chunks: covered.size,
           shipped: mc.commands, shippedParts: mc.parts,
           shown: document.getElementById("dpTicks").textContent.trim(),
           buildLoads: /\bfunction \S+:load\b/
             .test(files[`data/${ns}/function/build.mcfunction`] || ""),
           // `clear` used to start from wherever the player stood and
           // force-load nothing, which is why it never took the whole machine
           // clear used to start from wherever the player was standing and
           // force-load nothing, so it took away the part of the machine near
           // them and left the rest. It reads the recorded origin now — the
           // `with storage` is in `sys/clear_wait`, which is what opens the
           // gate onto the macro that positions the whole thing.
           clearAnchored:
             /\bfunction \S+:load\b/.test(files[`data/${ns}/function/clear.mcfunction`] || "")
             && /function \S+:sys\/clear_wait/.test(files[`data/${ns}/function/clear.mcfunction`] || "")
             && /function \S+:sys\/clear_at with storage \S+:origin/
                  .test(files[`data/${ns}/function/sys/clear_wait.mcfunction`] || ""),
           // /forceload marks chunks; it does not load them. Placing on the
           // next tick races the loader and drops most of the machine into
           // chunks that have not arrived — which is redstone on the floor.
           waitsForChunks: (() => {
             const build = files[`data/${ns}/function/build.mcfunction`] || "";
             const probe = files[`data/${ns}/function/sys/probe.mcfunction`] || "";
             // commands, not comments: the probe file explains itself in a
             // header that also says "if loaded", which counted as a probe
             const n = probe.split("\n")
               .filter(l => l.startsWith("execute if loaded")).length;
             return build.includes(`function ${ns}:sys/wait`)
                    && !/function \S+:tick/.test(build) && n === mc.chunks;
           })(),
           gadgets: Object.keys(files)
             .filter(k => k.includes("/function/move/")).length,
           // one labelled sign per control, hung after the machine is placed
           signs: (files[`data/${ns}/function/sys/signs.mcfunction`] || "")
             .split("\n").filter(l => l.startsWith("setblock")).length,
           wantSigns: mc.signs,
           signsHung: /function \S+:sys\/signs/
             .test(files[`data/${ns}/function/sys/done.mcfunction`] || ""),
           // the machine keeps ticking after the build and across a restart
           keepsChunks:
             /\bfunction \S+:load\b/
               .test(files[`data/${ns}/function/sys/done.mcfunction`] || "")
             && JSON.parse(files["data/minecraft/tags/function/load.json"] || "{}")
                  .values?.[0] === `${ns}:sys/keep`,
           supportsFirst: (() => {
             // the skeleton goes down before any redstone: walk the emitted
             // commands and refuse to find a component before the last support
             let lastSupport = -1, firstComponent = Infinity, i = 0;
             const sup = new Set(mc.palette.filter((_, k) => mc.support[k]));
             for (let p = 0; p < parts; p++) {
               const body = files[`data/${ns}/function/part/${String(p).padStart(4, "0")}.mcfunction`] || "";
               for (const line of body.split("\n")) {
                 if (!line) continue;
                 const st = line.replace(/ replace$/, "").split(" ").pop();
                 if (sup.has(st)) lastSupport = i;
                 else firstComponent = Math.min(firstComponent, i);
                 i++;
               }
             }
             return lastSupport < firstComponent;
           })(),
           readme: files["README.txt"] || "",
           kinds: kinds.size, zipKB: Math.round(zip.length / 1024),
           entries: Object.keys(back).length,
           mcmeta: new TextDecoder().decode(back["pack.mcmeta"]).replace(/\s+/g, " ") };
});
if (pack.bad) throw new Error(`${pack.bad} commands the game could not parse`);
if (pack.boxes)
  throw new Error(`${pack.boxes} fills vary on more than one axis — the ` +
                  `exporter's own replay test forbids boxes`);
if (pack.placed !== pack.blocks)
  throw new Error(`the datapack places ${pack.placed} blocks, the machine has ` +
                  `${pack.blocks}`);
if (pack.mismatched)
  throw new Error(`${pack.mismatched} blocks come back as a different block ` +
                  `state than the page is simulating`);
if (pack.entries < 10 || !/pack_format/.test(pack.mcmeta))
  throw new Error(`the zip does not reopen as a datapack: ${pack.entries} ` +
                  `entries, mcmeta ${pack.mcmeta}`);
if (pack.ns !== pack.namespace)
  throw new Error(`the page names the pack ${pack.ns}, the exporter names it ` +
                  `${pack.namespace} — the README's /function would not exist`);
if (pack.ctrl.join(",") !== pack.want.join(","))
  throw new Error(`control functions differ from the exporter: ` +
                  `[${pack.ctrl}] vs [${pack.want}]`);
if (!pack.copied)
  throw new Error(`a control function the page wrote is not the text the ` +
                  `exporter shipped — the page is generating them again`);
if (!pack.waitsForChunks)
  throw new Error(`the build does not wait for its chunks — it would race ` +
                  `the loader and place dust onto supports that never arrived`);
if (!pack.gadgets)
  throw new Error(`the gadgets are not in the pack, so this is two downloads`);
if (!pack.clearAnchored)
  throw new Error(`clear does not read the stored origin and force-load, so ` +
                  `it would only remove the part of the machine near the player`);
if (!pack.supportsFirst)
  throw new Error(`a redstone component is placed before the last support ` +
                  `block, so the game would drop it as an item`);
if (pack.signs !== pack.wantSigns || !pack.signs)
  throw new Error(`the pack hangs ${pack.signs} signs, the bundle says there ` +
                  `are ${pack.wantSigns} controls to label`);
if (!pack.signsHung)
  throw new Error(`the signs are never placed — sys/done does not run them`);
if (!pack.keepsChunks)
  throw new Error(`the chunks are not kept: the build does not re-assert its ` +
                  `force-load, or the world-start hook does not restore it`);
if (!pack.buildLoads)
  throw new Error(`build never calls load, so the machine is placed into ` +
                  `chunks the server is not simulating`);
if (pack.uncovered)
  throw new Error(`${pack.uncovered} chunks of the footprint are not ` +
                  `force-loaded — redstone does not tick there`);
if (!pack.readme.includes(`/function ${pack.ns}:build`))
  throw new Error(`the README does not tell the reader the command that works`);
// the page prints the tick count in prose; the exporter counted it independently
if (pack.shipped !== pack.commands || pack.shippedParts !== pack.parts)
  throw new Error(`the exporter counted ${pack.shipped} commands in ` +
                  `${pack.shippedParts} parts, the page generated ` +
                  `${pack.commands} in ${pack.parts}`);
if (pack.shown !== String(pack.parts))
  throw new Error(`the page's prose says the pack takes ${pack.shown} ticks, ` +
                  `it takes ${pack.parts}`);
console.log(`datapack: ${pack.commands.toLocaleString()} commands in ` +
            `${pack.parts} parts rebuild all ${pack.placed.toLocaleString()} ` +
            `blocks exactly (${pack.kinds} block states, ${pack.zipKB} KB zip, ` +
            `${pack.ms}ms in the page)`);
console.log(`  ${pack.ns}: ${pack.ctrl.length} functions copied byte-for-byte ` +
            `from the exporter (${pack.gadgets} of them gadgets in the same ` +
            `pack), ${pack.chunks.toLocaleString()} chunks force-loaded and ` +
            `waited for and kept loaded, none of the footprint left out, the ` +
            `whole skeleton placed before any redstone, ${pack.signs} controls ` +
            `labelled, clear anchored to the stored origin`);

// --- nothing the animation touches may be left invisible -------------------
// Every reveal is a GSAP `from` tween, which means the start state is written
// by script and the resting document is already the finished page. The failure
// to guard against is a tween that starts an element at opacity 0 and never
// finishes it — a ScrollTrigger that never fires, a timeline that throws
// halfway. So this scrolls the whole page, top to bottom and back, and then
// insists that nothing anyone is meant to read is transparent.
const reveal = await page.evaluate(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const h = document.body.scrollHeight;
  for (let y = 0; y <= h; y += Math.round(innerHeight * 0.8)) {
    scrollTo({ top: y, behavior: "instant" }); await wait(120);
  }
  scrollTo({ top: h, behavior: "instant" }); await wait(700);
  scrollTo({ top: 0, behavior: "instant" }); await wait(700);
  const content = [...document.querySelectorAll(
    "header.top .wrap > *, .rig, .rig-note, main section > *, " +
    ".cards > *, footer .wrap")];
  const invisible = content.filter(e => {
    const cs = getComputedStyle(e);
    return cs.display !== "none" && +cs.opacity < 0.99;
  }).map(e => (e.className || e.tagName).toString().slice(0, 30));
  return { n: content.length, invisible: [...new Set(invisible)] };
});
if (reveal.invisible.length)
  throw new Error(`${reveal.invisible.length} elements are still not fully ` +
                  `visible after scrolling the page: ${reveal.invisible.slice(0, 4).join(", ")}`);
console.log(`arrival: ${reveal.n} animated elements, every one fully visible ` +
            `after a pass down the page and back`);

// --- typing a number moves the right levers -------------------------------
// The bit switches are the truth, but nobody spells 723 out of ten of them, so
// each operand has a decimal box that drives the same levers. If that mapping
// is wrong the machine still computes correctly and answers the wrong question,
// which is the worst kind of broken, so it is checked against the levers
// themselves rather than against the box.
const dial = await page.evaluate(() => {
  const out = [];
  for (const [pad, want] of [["A", 723], ["B", 300], ["A", 0], ["B", 1023]]) {
    const box = document.querySelector("#val" + pad);
    box.value = String(want);
    box.dispatchEvent(new Event("change"));
    out.push({ pad, want, levers: bitsOf(pad), box: box.value });
  }
  // and it must refuse what the machine cannot hold
  const box = document.querySelector("#valA");
  box.value = "9999"; box.dispatchEvent(new Event("change"));
  const clamped = bitsOf("A");
  box.value = "-5"; box.dispatchEvent(new Event("change"));
  const floored = bitsOf("A");
  return { out, clamped, floored };
});
for (const r of dial.out)
  if (r.levers !== r.want || +r.box !== r.want)
    throw new Error(`typing ${r.want} into ${r.pad} set the levers to ` +
                    `${r.levers} and the box to ${r.box}`);
if (dial.clamped !== 1023 || dial.floored !== 0)
  throw new Error(`out-of-range entry landed on ${dial.clamped} and ` +
                  `${dial.floored}, not 1023 and 0`);
console.log(`operand entry: ${dial.out.map(r => r.pad + "=" + r.want).join(" ")} ` +
            `each spelled out on the levers, out of range clamps to 0..1023`);

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


// --- the atlas has a tile for everything that asks for one ----------------
// A tile index past the end of the atlas samples whatever happens to be there,
// which on a 128x128 texture is another block's texture rather than an error.
// Rebuild the atlas here and insist that every tile any instance names is both
// inside it and not blank.
const atlas = await page.evaluate(() => {
  const px = makeAtlas(), used = new Set(), n = ATLAS_TILES * ATLAS_TILES;
  for (let j = 0; j < inst.length; j++) used.add(tiles[j]);
  const bad = [], blank = [], code = [];
  let coloured = 0;
  for (const t of used) {
    if (t < 0 || t >= n) { bad.push(t); continue; }
    const ox = (t % ATLAS_TILES) * TX, oy = ((t / ATLAS_TILES) | 0) * TX;
    let ink = 0, own = 0;
    for (let y = 0; y < TX; y++) for (let x = 0; x < TX; x++) {
      const a = px[((oy + y) * ATLAS_PX + ox + x) * 4 + 3];
      // alpha is a code, not a coverage: 0 is empty, 64 is "shade only, the
      // block's state colours it", 255 is "this texel owns its colour".
      // Anything below the state code would be discarded by the shader.
      if (a > 0 && a < 60) code.push([t, a]);
      if (a >= 60) ink++;
      if (a > 200) own++;
    }
    if (!ink) blank.push(t);
    if (own) coloured++;
  }
  return { used: used.size, capacity: n, bad, blank, code, coloured,
           bytes: px.length };
});
if (atlas.bad.length)
  throw new Error(`tiles outside the atlas: ${atlas.bad.join(", ")}`);
if (atlas.blank.length)
  throw new Error(`tiles drawn from blank atlas cells: ${atlas.blank.join(", ")}`);
if (atlas.code.length)
  throw new Error(`texels with an alpha the shader would discard: ` +
                  atlas.code.slice(0, 4).map(([t, a]) => `tile ${t} a=${a}`).join(", "));
if (!atlas.coloured)
  throw new Error("no tile carries a colour of its own — the atlas has gone " +
                  "back to being a luminance mask");
console.log(`atlas: ${atlas.used} of ${atlas.capacity} tiles used, ` +
            `${atlas.coloured} with colours of their own, all inside the sheet ` +
            `and none blank (${(atlas.bytes / 1024) | 0} KB, built in the page)`);


// --- dust is drawn pointing the way the simulator says it points ----------
// A wire's shape is not decoration: a dot powers nothing horizontally, a
// straight line powers the block behind it, and which of those a cell is
// decides whether the circuit works. The renderer picks its tile from
// `eng.points`, the same four-bit mask the power rules are evaluated from, so
// this checks the drawing against the simulation rather than against itself.
const shapes = await page.evaluate(() => {
  const hist = {}, seen = new Set();
  let wires = 0, wrong = 0, straightOnAxis = 0, dots = 0;
  for (let j = 0; j < inst.length; j++) {
    const i = inst[j];
    if (world.kind[i] !== K_WIRE) continue;
    wires++;
    const mask = eng.points[i] & 15;
    if (tiles[j] !== T_DUST + mask) wrong++;
    hist[mask] = (hist[mask] || 0) + 1;
    seen.add(mask);
    if (mask === 0) dots++;
    // 3 is north+south, 12 is west+east: a straight run, which is what a rail
    // or a collector between its taps should be
    if (mask === 3 || mask === 12) straightOnAxis++;
  }
  return { wires, wrong, dots, straightOnAxis, kinds: seen.size,
           top: Object.entries(hist).sort((a, b) => b[1] - a[1]).slice(0, 4) };
});
if (shapes.wrong)
  throw new Error(`${shapes.wrong} wires drawn with the wrong connection tile`);
if (shapes.kinds < 4)
  throw new Error(`only ${shapes.kinds} distinct wire shapes in the whole ` +
                  `machine — the mask is probably not reaching the renderer`);
if (shapes.straightOnAxis < shapes.wires * 0.5)
  throw new Error("most dust should be straight run, saw " +
                  `${shapes.straightOnAxis}/${shapes.wires}`);
console.log(`dust: ${shapes.wires.toLocaleString()} wires, ` +
            `${shapes.kinds} distinct shapes, every one matching eng.points; ` +
            `${shapes.straightOnAxis.toLocaleString()} straight, ` +
            `${shapes.dots.toLocaleString()} dots ` +
            `(commonest: ${shapes.top.map(([m, n]) => `${m}x${n}`).join(" ")})`);


// --- coloured light is live, and costs nothing when nothing is lit --------
// The light volume is baked on the CPU and uploaded as a texture, and it is
// only rebuilt when a *light* changes — a settling wavefront repaints thousands
// of dust cells a tick and none of them emit. Both halves of that are worth
// asserting: that torches switching moves the volume, and that dust moving on
// its own does not make it re-bake.
const light = await page.evaluate(() => {
  const sum = () => {
    let s = 0;
    for (let i = 0; i < lightPix.length; i += 7) s += lightPix[i];
    return s;
  };
  bakeLight();
  const before = sum();
  // burn a torch out by hand: the instance's colour changes, so its light must
  const j = glowIdx[(glowIdx.length / 2) | 0];
  const i = inst[j];
  const wasLit = world.lit[i];
  world.lit[i] = 0;
  bakeLight();
  const dark = sum();
  world.lit[i] = wasLit;
  bakeLight();
  const back = sum();

  // and now the cheap half: mark a dust instance dirty and check the volume is
  // not asked to rebuild for it
  let dust = -1;
  for (let k = 0; k < inst.length && dust < 0; k++)
    if (world.kind[inst[k]] === K_WIRE) dust = k;
  lightDirty = false;
  dirty.add(dust);
  flushDirty();
  const dustAsks = lightDirty;
  lightDirty = false;
  dirty.add(j);
  flushDirty();
  const lightAsks = lightDirty;
  return { before, dark, back, dustAsks, lightAsks,
           cell: lg.cell, tex: `${lg.tw}x${lg.th}` };
});
if (!(light.dark < light.before))
  throw new Error("putting a torch out did not dim the light volume");
if (light.back !== light.before)
  throw new Error("relighting it did not restore the light volume");
if (light.dustAsks)
  throw new Error("a dust cell changing asked the light volume to rebuild");
if (!light.lightAsks)
  throw new Error("a torch changing did NOT ask the light volume to rebuild");
console.log(`light: ${light.cell}-block cells in a ${light.tex} atlas; ` +
            `a torch going out dims it (${light.before} -> ${light.dark}) ` +
            `and only lights ask it to rebuild`);


// --- the viewport is actually drawing -------------------------------------
// A smoke test, not a metric. It used to report the colour count from whatever
// camera the checks above happened to leave behind, which swung it threefold
// run to run and made it look like a measurement of something. Snap to a known
// view, and assert the one thing it can actually prove: that the frame is not
// blank. A shader that fails to compile, a buffer that never uploads and a
// camera aimed at nothing all end here.
const px = await page.evaluate(() => {
  focus("all", 0);
  const c = document.getElementById("cv"), g = c.getContext("webgl");
  draw();     // no preserveDrawingBuffer: read back in the same task as the draw
  const buf = new Uint8Array(c.width * c.height * 4);
  g.readPixels(0, 0, c.width, c.height, g.RGBA, g.UNSIGNED_BYTE, buf);
  const seen = new Set();
  let ink = 0;
  const bg = buf[0] * 65536 + buf[1] * 256 + buf[2];
  for (let i = 0; i < buf.length; i += 4) {
    const v = buf[i] * 65536 + buf[i + 1] * 256 + buf[i + 2];
    seen.add(v);
    if (v !== bg) ink++;
  }
  return { distinctColours: seen.size, litFraction: +(ink / (buf.length / 4)).toFixed(3),
           w: c.width, h: c.height };
});
if (px.distinctColours < 500 || px.litFraction < 0.05)
  throw new Error(`the frame is essentially blank: ${px.distinctColours} ` +
                  `colours, ${px.litFraction * 100}% of pixels not background`);
// litFraction is the stable half — the geometry drawn is the same every run.
// distinctColours is not, and cannot be: the page animates between these
// evaluate() calls, so the machine has settled a different amount each time
// and the dust is at different levels. It is here to look at, not to compare.
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
// Screenshots are for looking at after a run, not for keeping: `out/*.png` is
// ignored. Tracking these meant every test run dirtied the tree and every
// commit carried a few hundred kilobytes of binary nobody ever opened.
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
