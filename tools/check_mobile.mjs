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
await touch("pointerdown", 3, mid.x, mid.y, true);
await touch("pointerup", 3, mid.x + 4, mid.y + 3, true);   // fingers wobble
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
    if (r.width < 28 || r.height < 28)
      small.push(`${el.id || el.className}=${Math.round(r.width)}×${Math.round(r.height)}`);
    if (el.closest(".rig") && (r.right > rig.right + 0.5 || r.left < rig.left - 0.5))
      clipped.push(`${el.id || el.className}`);
  }
  return { overflow: document.documentElement.scrollWidth - innerWidth,
           tooSmall: small, clipped };
});
want(layout.overflow <= 0, `portrait overflows by ${layout.overflow}px`);
want(!layout.tooSmall.length,
     `tap targets under 28px: ${layout.tooSmall.slice(0, 6).join(", ")}`);
want(!layout.clipped.length,
     `clipped by the rig: ${layout.clipped.slice(0, 6).join(", ")}`);
if (layout.overflow <= 0 && !layout.tooSmall.length && !layout.clipped.length)
  console.log("layout: nothing overflows, nothing clipped, every control ≥28px");

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
