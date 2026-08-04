/* Verify docs/preview.html on a phone.

   The desktop check drives the machine; this one drives the *hands*. Every
   assertion here corresponds to something that was actually broken on a phone:
   there was no way to zoom at all, a second finger lurched the camera instead
   of pinching, tapping a block reported nothing, `setPointerCapture` threw on
   every touch, and turning the phone sideways left the view fitted to the old
   aspect ratio.

   Run from the repository root: node tools/check_mobile.mjs */
import { chromium, devices } from "playwright";
import path from "path";

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ ...devices["iPhone 13"], colorScheme: "dark" });
const errs = [];
page.on("pageerror", e => errs.push("pageerror: " + e.message));
page.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text()); });
const fail = [];
const want = (ok, msg) => { if (!ok) fail.push(msg); };
/* The view now has memory — a flick keeps turning, a view change flies, a tap
   arms the next one as a possible double. Each assertion below is about one
   gesture, so quiet all of that between them; otherwise the tests measure each
   other. */
const calm = () => page.evaluate(() => {
  spinAz = spinEl = 0; fly = null; lastTap = 0; lastHit = -1; lastTapAt = null;
});

const t0 = Date.now();
await page.goto("file://" + path.resolve("docs/preview.html"));
await page.waitForFunction("window.__ready === true", null,
                           { timeout: 300000, polling: 250 });
const boot = (Date.now() - t0) / 1000;
console.log(`boot: ${boot.toFixed(1)}s`);

const cost = await page.evaluate(() => {
  let t = performance.now(); buildInstances();
  const rebuild = performance.now() - t;
  t = performance.now(); const pts = allPoints();
  return { rebuildMs: +rebuild.toFixed(0), framePoints: pts.length,
           framingMs: +(performance.now() - t).toFixed(1),
           instances: inst.length };
});
console.log("cost:", cost);
want(cost.rebuildMs < 900, `rebuilding instances takes ${cost.rebuildMs}ms`);
// what matters is that framing is cheap, not how it gets there: the box
// corners were free and framed the box rather than the machine
want(cost.framingMs < 12, `framing a view takes ${cost.framingMs}ms`);

// --- gestures --------------------------------------------------------------
const box = await page.locator("#cv").boundingBox();
const mid = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
const touch = (ev, id, x, y, primary) => page.locator("#cv").dispatchEvent(ev,
  { pointerId: id, pointerType: "touch", isPrimary: !!primary,
    clientX: x, clientY: y, buttons: ev === "pointerup" ? 0 : 1 });

await page.evaluate(() => { window.__c = { ...cam }; });
await touch("pointerdown", 1, mid.x, mid.y, true);
await touch("pointermove", 1, mid.x + 60, mid.y + 20, true);
await touch("pointerup", 1, mid.x + 60, mid.y + 20, true);
const orbit = await page.evaluate(() => ({
  az: cam.az - window.__c.az, el: cam.el - window.__c.el,
  dist: cam.dist - window.__c.dist }));
want(Math.abs(orbit.az) > 0.1 && Math.abs(orbit.el) > 0.01, "one finger does not orbit");
want(orbit.dist === 0, "one finger changed the zoom");
console.log("one finger orbits, and only orbits");

// --- and a swipe up the canvas reads on, rather than tilting -------------
// The canvas is over half a phone's screen and takes every touch, so before
// this the only way past the instrument was to find the strip of page beside
// it: a straight-up swipe moved the page 0 pixels and tilted the camera 0.37
// radians, which is what a broken page looks like. A gesture is decided by the
// direction it commits to, so both of these have to keep working.
await calm();
const swipeBy = async (dx, dy, id) => {
  const home = await page.evaluate(() => scrollY);
  await page.evaluate(() => { document.querySelector("#view").scrollIntoView({ block: "center" }); });
  await page.waitForTimeout(300);
  const b = await page.locator("#cv").boundingBox();
  const m = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  const was = await page.evaluate(() => ({ y: scrollY, az: cam.az, el: cam.el }));
  await touch("pointerdown", id, m.x, m.y, true);
  for (let i = 1; i <= 12; i++) await touch("pointermove", id, m.x + dx * i, m.y + dy * i, true);
  await touch("pointerup", id, m.x + dx * 12, m.y + dy * 12, true);
  await page.waitForTimeout(420);
  const out = await page.evaluate(w => ({ scrolled: scrollY - w.y, az: cam.az - w.az,
                                          el: cam.el - w.el }), was);
  // put the page back where it was found: everything after this measures the
  // canvas by coordinates taken once, at the top
  await page.evaluate(y => scrollTo({ top: y, behavior: "instant" }), home);
  await page.waitForTimeout(200);
  return out;
};
// a reader arriving cold: the canvas has to let them past
await page.evaluate(() => { engagedUntil = 0; });
const up = await swipeBy(0, -13, 21);
want(up.scrolled > 100, `a swipe up the canvas scrolled ${Math.round(up.scrolled)}px`);
want(up.az === 0 && up.el === 0, "a swipe up the canvas moved the camera as well");
const turn = await swipeBy(13, 0, 22);
want(turn.scrolled === 0, `a sideways drag scrolled the page ${Math.round(turn.scrolled)}px`);
want(Math.abs(turn.az) > 0.1, "a sideways drag did not turn the machine");
console.log(`a swipe reads on (${Math.round(up.scrolled)}px, camera still), ` +
            `a drag turns (${turn.az.toFixed(2)} rad, page still)`);

// ...and someone plainly driving the machine has to be able to tilt it without
// the page sliding out from under them. Tilting *is* a vertical drag, which is
// why direction alone was not enough: a canvas that has just been turned stays
// engaged, and one finger orbits until that lapses. The whole gesture goes in
// one page task, because a round trip per pointermove in this container takes
// longer than the engagement lasts.
const tilt = await page.evaluate(() => {
  const r = cv.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const ev = (t, cx, cy, bt) => cv.dispatchEvent(new PointerEvent(t,
    { pointerId: 77, pointerType: "touch", isPrimary: true, bubbles: true,
      clientX: cx, clientY: cy, buttons: bt }));
  // Turn it first, so the canvas is engaged. The release has to be where the
  // finger *ended*: let go back at the start and the page rightly reads the
  // whole thing as a tap, which is what this check did on its first outing.
  ev("pointerdown", x, y, 1);
  ev("pointermove", x + 40, y, 1);
  ev("pointerup", x + 40, y, 0);
  const was = { y: scrollY, el: cam.el,
                left: Math.round(engagedUntil - performance.now()) };
  ev("pointerdown", x, y, 1);
  for (let i = 1; i <= 12; i++) ev("pointermove", x, y - i * 13, 1);
  ev("pointerup", x, y - 156, 0);
  showPick(-1);
  return { engaged: was.left, scrolled: scrollY - was.y,
           tilted: Math.abs(cam.el - was.el) };
});
want(tilt.engaged > 0, "the canvas did not stay engaged after being turned");
want(tilt.scrolled === 0,
     `tilting an engaged canvas scrolled the page ${tilt.scrolled}px`);
console.log(`while you are driving it (${tilt.engaged}ms left), a vertical drag ` +
            `tilts by ${tilt.tilted.toFixed(2)} rad and the page holds still`);

await calm();
await page.evaluate(() => { window.__c = { ...cam }; });
await touch("pointerdown", 1, mid.x - 40, mid.y, true);
await touch("pointerdown", 2, mid.x + 40, mid.y, false);
await touch("pointermove", 1, mid.x - 100, mid.y, true);
await touch("pointermove", 2, mid.x + 100, mid.y, false);
const open = await page.evaluate(() => ({
  ratio: cam.dist / window.__c.dist, az: cam.az - window.__c.az }));
await touch("pointermove", 1, mid.x - 20, mid.y, true);
await touch("pointermove", 2, mid.x + 20, mid.y, false);
const shut = await page.evaluate(() => cam.dist / window.__c.dist);
await touch("pointerup", 1, mid.x - 20, mid.y, true);
await touch("pointerup", 2, mid.x + 20, mid.y, false);
want(open.ratio < 0.9, `spreading two fingers did not zoom in (×${open.ratio.toFixed(2)})`);
want(shut > 1.1, `pinching together did not zoom out (×${shut.toFixed(2)})`);
want(Math.abs(open.az) < 1e-9, "a pinch also rotated the camera");
want(!(await page.evaluate(() => document.getElementById("inspect").textContent)),
     "letting go of a pinch was treated as a tap");
console.log(`pinch: open ×${open.ratio.toFixed(2)}, close ×${shut.toFixed(2)}, no drift`);

// --- two fingers also pan, and a flick keeps turning ------------------------
await calm();
await page.evaluate(() => { focus("ctrl", 0); window.__t = target.slice(); });
await touch("pointerdown", 11, mid.x - 40, mid.y, true);
await touch("pointerdown", 12, mid.x + 40, mid.y, false);
await touch("pointermove", 11, mid.x + 30, mid.y + 30, true);
await touch("pointermove", 12, mid.x + 110, mid.y + 30, false);
await touch("pointerup", 11, mid.x + 30, mid.y + 30, true);
await touch("pointerup", 12, mid.x + 110, mid.y + 30, false);
const panned = await page.evaluate(() =>
  target.some((v, i) => Math.abs(v - window.__t[i]) > 0.5));
want(panned, "two fingers did not pan the view");
if (panned) console.log("two fingers pan as well as pinch");

// The whole flick goes in one page task: this container renders in software,
// which starves setTimeout inside the page for seconds at a time, so anything
// timed from Node measures the renderer rather than the interaction.
const flick = await page.evaluate(() => {
  focus("ctrl", 0); spinAz = spinEl = 0;
  const r = cv.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
  const ev = (t, cx, bt) => cv.dispatchEvent(new PointerEvent(t,
    { pointerId: 91, pointerType: "touch", isPrimary: true, bubbles: true,
      clientX: cx, clientY: y, buttons: bt }));
  ev("pointerdown", x, 1); ev("pointermove", x + 18, 1); ev("pointerup", x + 18, 0);
  const spun = spinAz;
  const before = cam.az;
  for (let i = 0; i < 200; i++) stepSpin();   // drive the decay directly
  return { spun, capped: Math.abs(spun) <= 0.0551,
           turned: Math.abs(cam.az - before) > 1e-3, rest: spinAz === 0 };
});
want(Math.abs(flick.spun) > 1e-4, "letting go mid-drag did not keep the view turning");
want(flick.capped, `a flick span at ${flick.spun.toFixed(3)} rad/frame`);
want(flick.turned && flick.rest, "the spin did not decay to rest");
if (flick.turned && flick.rest) console.log("a flick keeps turning, then settles");

// --- a tap anywhere over the machine reads a block --------------------------
await calm();
await page.evaluate(() => { focus("ctrl", 0); showPick(-1); });
await page.waitForTimeout(300);
// measured fresh rather than reused: a check that silently taps empty space
// because something earlier scrolled the page is a check that proves nothing
const tapBox = await page.locator("#cv").boundingBox();
const tapAt = { x: tapBox.x + tapBox.width / 2, y: tapBox.y + tapBox.height / 2 };
await touch("pointerdown", 3, tapAt.x, tapAt.y, true);
await touch("pointerup", 3, tapAt.x + 4, tapAt.y + 3, true);   // fingers wobble
const read = await page.evaluate(() => document.getElementById("inspect").textContent);
want(!!read, "tapping the machine read nothing");
if (read) console.log(`tap reads: "${read}"`);

// --- a second tap in the same place goes and looks at the block -------------
await calm();
const dbl = await page.evaluate(() => {
  focus("ctrl", 0);
  const before = cam.dist, tgt = target.slice();
  const r = cv.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
  const ev = (t, cx, id, bt) => cv.dispatchEvent(new PointerEvent(t,
    { pointerId: id, pointerType: "touch", isPrimary: true, bubbles: true,
      clientX: cx, clientY: y, buttons: bt }));
  ev("pointerdown", x, 81, 1); ev("pointerup", x, 81, 0);
  const oneTapFlew = !!fly;
  ev("pointerdown", x + 1, 82, 1); ev("pointerup", x + 1, 82, 0);
  return { oneTapFlew, flew: !!fly, closer: fly ? fly.d1 < before : false,
           moved: fly ? fly.g1.some((v, i) => Math.abs(v - tgt[i]) > 0.5) : false };
});
want(!dbl.oneTapFlew, "a single tap moved the camera");
want(dbl.flew && dbl.closer && dbl.moved,
     "a second tap in the same place did not fly to the block");
if (dbl.flew && dbl.closer) console.log("double tap flies to the block");

// --- rotation refits the view ----------------------------------------------
await calm();
await page.evaluate(() => { focus("all", 0); window.__d = cam.dist; window.__az = cam.az; });
await page.setViewportSize({ width: 844, height: 390 });
// the refit is debounced, and a viewport change can fire resize more than once,
// which restarts it — so poll rather than guess a wait
let rot = null;
for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(100);
  rot = await page.evaluate(() => ({
    refitted: cam.dist !== window.__d, keptAngle: cam.az === window.__az,
    overflow: document.documentElement.scrollWidth - innerWidth }));
  if (rot.refitted) break;
}
want(rot.refitted, "turning the phone left the view fitted to the old aspect");
want(rot.keptAngle, "the refit moved the camera the viewer had aimed");
want(rot.overflow <= 0, `landscape overflows by ${rot.overflow}px`);
if (rot.refitted && rot.keptAngle)
  console.log("rotation refits and keeps the viewer's angle");

// --- and sideways is a layout of its own ------------------------------------
// A phone held sideways is wider than the narrow breakpoint and shorter than
// anything else, so the width rules only half applied: it spent two thirds of a
// 390px screen on a headline before showing any machine. Height is the scarce
// axis here, and this is what says so.
const land = await page.evaluate(() => {
  const r = document.querySelector("#view").getBoundingClientRect();
  return { top: Math.round(r.top), h: Math.round(r.height), vh: innerHeight,
           h1: Math.round(parseFloat(getComputedStyle(document.querySelector("h1")).fontSize)),
           cols: getComputedStyle(document.querySelector(".bar")).gridTemplateColumns
                   .split(" ").length };
});
want(land.top < land.vh * 0.62,
     `sideways, the machine starts ${land.top}px into a ${land.vh}px screen`);
want(land.h > land.vh * 0.6,
     `sideways, the view is ${land.h}px of ${land.vh}`);
want(land.h1 <= 26, `sideways, the headline is still ${land.h1}px`);
want(land.cols === 2, "sideways, the control bar did not use the width it has");
console.log(`sideways: headline ${land.h1}px, view starts ${land.top}px into ` +
            `${land.vh} and takes ${land.h}, controls side by side`);

// --- the view is bounded in pixels, not just in viewport units --------------
// This page ships as an artifact, which means an iframe whose height is set
// from the content inside it. A viewport-height unit there is circular: the
// canvas grows, the document grows, the frame grows, and the canvas is a
// percentage of the frame. Measured before the caps, a phone-width frame took
// the canvas to 5,598px, then 7,707px, with the frame past 14,000 and climbing.
// A very tall viewport is the same condition without the iframe.
for (const [w, h] of [[390, 12000], [844, 9000], [1200, 20000]]) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(350);
  const view = await page.evaluate(() =>
    Math.round(document.querySelector("#view").getBoundingClientRect().height));
  want(view <= 600, `in a ${w}x${h} viewport the view is ${view}px — a height ` +
                    `in viewport units with no pixel cap runs away inside a ` +
                    `frame that is sized from its content`);
}
console.log("the view stays bounded in a viewport 20,000px tall");

// --- the answer follows you down the page -----------------------------------
// On a phone the controls sit a screen below the readout, so pressing an
// operation sends the answer off the top of the display and the reader is left
// working a keypad with no idea what the machine is doing. The peek bar only
// appears once the readout has actually gone, and it takes you back.
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
const peek = await page.evaluate(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  scrollTo({ top: 0, behavior: "instant" }); await wait(500);
  const atTop = document.querySelector("#peek").hidden;
  document.querySelector(".transport").scrollIntoView({ block: "center" });
  await wait(900);
  const el = document.querySelector("#peek");
  const shown = !el.hidden;
  const answerGone = document.querySelector(".answer").getBoundingClientRect().bottom < 0;
  const r = el.getBoundingClientRect();
  el.click(); await wait(1200);
  const back = Math.abs(document.querySelector(".rig").getBoundingClientRect().top) < 90;
  return { atTop, shown, answerGone, back, h: Math.round(r.height) };
});
want(peek.atTop, "the peek bar is showing while the readout is still on screen");
want(peek.answerGone && peek.shown,
     "the readout scrolled away and nothing followed it down");
want(peek.h >= 44, `the peek bar is only ${peek.h}px tall`);
want(peek.back, "tapping the peek bar did not bring the machine back");
console.log(`peek bar: hidden at the top, ${peek.h}px once the readout goes, ` +
            `and it scrolls back to the machine`);

// --- layout -----------------------------------------------------------------
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
const layout = await page.evaluate(() => {
  const small = [], clipped = [];
  // `.rig` hides its overflow so its corners can be round, which means a
  // control can slide out of sight without the document ever getting wider —
  // exactly how bit 0 went missing. Check against the container, not the page.
  const rig = document.querySelector(".rig").getBoundingClientRect();
  for (const el of document.querySelectorAll("button,select,input[type=range]")) {
    const r = el.getBoundingClientRect();
    if (!r.width) continue;
    // Depth of target, not area: ten bit switches cannot each be 44 wide on a
    // 390px phone, but they can all be 40 tall, and for a row you sweep along
    // the height is what your thumb is aiming at. 40 is the floor for the
    // dimension that has room, 28 for the one that does not.
    if (Math.max(r.width, r.height) < 40 || Math.min(r.width, r.height) < 28)
      small.push(`${el.id || el.className}=${Math.round(r.width)}×${Math.round(r.height)}`);
    if (el.closest(".rig") && (r.right > rig.right + 0.5 || r.left < rig.left - 0.5))
      clipped.push(`${el.id || el.className}`);
  }
  return { overflow: document.documentElement.scrollWidth - innerWidth,
           tooSmall: small, clipped };
});
want(layout.overflow <= 0, `portrait overflows by ${layout.overflow}px`);
want(!layout.tooSmall.length,
     `tap targets too small: ${layout.tooSmall.slice(0, 6).join(", ")}`);
want(!layout.clipped.length,
     `clipped by the rig: ${layout.clipped.slice(0, 6).join(", ")}`);
if (layout.overflow <= 0 && !layout.tooSmall.length && !layout.clipped.length)
  console.log("layout: nothing overflows, nothing clipped, every control at " +
            "least 40px in the dimension that has room");

// --- one more sanity pass: 375px, the narrowest phone still in use ----------
await page.setViewportSize({ width: 375, height: 667 });
await page.waitForTimeout(400);
const narrow = await page.evaluate(() => {
  const rig = document.querySelector(".rig").getBoundingClientRect();
  let out = 0;
  for (const el of document.querySelectorAll(".rig button,.rig input")) {
    const r = el.getBoundingClientRect();
    if (r.width && (r.right > rig.right + 0.5 || r.left < rig.left - 0.5)) out++;
  }
  return { overflow: document.documentElement.scrollWidth - innerWidth, clipped: out };
});
want(narrow.overflow <= 0, `375px wide overflows by ${narrow.overflow}px`);
want(!narrow.clipped, `375px wide clips ${narrow.clipped} controls`);
if (narrow.overflow <= 0 && !narrow.clipped) console.log("375px: clean");

for (const e of errs) fail.push(e);
console.log(fail.length ? "FAIL:\n  " + fail.join("\n  ") : "PASS");
await browser.close();
process.exit(fail.length ? 1 : 0);
