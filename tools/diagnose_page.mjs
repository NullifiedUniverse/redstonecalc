/* Measure the page as an experience rather than as a set of assertions.

   `check_preview.mjs` answers "is anything broken". This answers "what is it
   like to arrive at this page and try to use it" — how far you scroll before
   the machine is on screen, how long the primary button blocks for, whether the
   progress meter the settle has ever actually appears during the flow a first
   visitor takes, how much of the first screen is writing versus instrument.

   It asserts almost nothing. It prints numbers to argue from.

   Run from the repository root: node tools/diagnose_page.mjs */
import { chromium, devices } from "playwright";
import path from "path";

const out = {};
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });

async function open(profile, label) {
  const page = await browser.newPage(profile);
  const t0 = Date.now();
  page.setDefaultNavigationTimeout(180000);
  await page.goto("file://" + path.resolve("docs/preview.html"));
  await page.waitForFunction("window.__ready === true", null,
                             { timeout: 300000, polling: 250 });
  out[label] = { bootSeconds: +((Date.now() - t0) / 1000).toFixed(1) };
  return page;
}

// --- desktop -------------------------------------------------------------
const page = await open({ viewport: { width: 1400, height: 900 } }, "desktop");
const d = out.desktop;

// What is on the first screen before anyone scrolls?
Object.assign(d, await page.evaluate(() => {
  const vh = innerHeight;
  const seen = el => {
    const r = el.getBoundingClientRect();
    const top = Math.max(0, r.top), bot = Math.min(vh, r.bottom);
    return Math.max(0, bot - top);
  };
  const view = document.querySelector("#view");
  const rig = document.querySelector(".rig");
  const bar = document.querySelector(".bar");
  return {
    viewport: vh,
    machineTop: Math.round(view.getBoundingClientRect().top),
    machineVisible: Math.round(seen(view)),
    controlsVisible: Math.round(seen(bar)),
    rigFitsInFirstScreen: rig.getBoundingClientRect().bottom <= vh,
    pageHeight: document.body.scrollHeight,
    screensOfPage: +(document.body.scrollHeight / vh).toFixed(1),
  };
}));

// The primary action, timed. This is the button the page tells you to press.
Object.assign(d, await page.evaluate(() => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const time = fn => { const t = performance.now(); fn(); return performance.now() - t; };
  for (let i = 0; i < circ.width; i++) {
    if (!!world.lit[switchIdx["A" + i]] !== !!((723 >> i) & 1)) toggleBit("A", i);
    if (!!world.lit[switchIdx["B" + i]] !== !!((300 >> i) & 1)) toggleBit("B", i);
  }
  settleNow();
  const railBefore = !document.querySelector("#rail").hidden;
  pressButton("0");
  const railAfterPress = !document.querySelector("#rail").hidden;
  const blockedMs = time(() => settleNow());
  return { settleBlocksMs: +blockedMs.toFixed(1), railBefore, railAfterPress,
           railAfterSettle: !document.querySelector("#rail").hidden,
           answer: readValue(), ticks: eng.now };
}));

// How long does one animated tick cost, and what does `play` feel like?
Object.assign(d, await page.evaluate(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < circ.width; i++)
    if (!!world.lit[switchIdx["A" + i]] !== !!((999 >> i) & 1)) toggleBit("A", i);
  settleNow();
  pressButton("0");
  const t0 = performance.now();
  let n = 0;
  while (eng.queue.length && n < 400) { stepTick(); n++; }
  const per = (performance.now() - t0) / Math.max(1, n);
  const railUp = !document.querySelector("#rail").hidden;
  const pct = document.querySelector("#railPct").textContent;
  const drained = !eng.queue.length;
  settleNow();
  return { msPerAnimatedTick: +per.toFixed(3), railUpDuringPlay: railUp,
           meterAfter400Ticks: pct, queueEmptyAfter400: drained };
}));

// Frame cost while drawing the whole machine
Object.assign(d, await page.evaluate(async () => {
  const t = performance.now();
  let f = 0;
  await new Promise(res => {
    const tick = () => { invalidate(); f++;
      if (performance.now() - t > 1000) return res();
      requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
  return { fps: f };
}));

// Reading load: how much prose, how many numbers
Object.assign(d, await page.evaluate(() => {
  const words = document.querySelector("main").innerText.split(/\s+/).length;
  return { notebookWords: words,
           sections: document.querySelectorAll("main section").length,
           tables: document.querySelectorAll("main table").length,
           controls: document.querySelectorAll(".rig button,.rig input,.rig select").length };
}));
await page.close();

// --- phone ---------------------------------------------------------------
const m = await open({ ...devices["iPhone 13"] }, "phone");
Object.assign(out.phone, await m.evaluate(() => {
  const vh = innerHeight;
  const view = document.querySelector("#view");
  const bar = document.querySelector(".bar");
  const ops = document.querySelector(".ops");
  return {
    viewport: vh,
    machineTop: Math.round(view.getBoundingClientRect().top),
    // how far down is the button the page tells you to press?
    settleButtonTop: Math.round(
      document.querySelector("#bSettle").getBoundingClientRect().top),
    opsTop: Math.round(ops.getBoundingClientRect().top),
    screensToReachOps: +(ops.getBoundingClientRect().top / vh).toFixed(2),
    screensOfPage: +(document.body.scrollHeight / vh).toFixed(1),
  };
}));
await m.close();

console.log(JSON.stringify(out, null, 2));
await browser.close();
