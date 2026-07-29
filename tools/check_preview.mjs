/* Verify docs/preview.html in a real browser: the page's own simulator has to
   reproduce the Python result — all 100 single-digit sums, read off the lamps.
   Run from the repository root with `node tools/check_preview.mjs`. */
import { chromium } from "playwright";
import path from "path";

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errs = [];
page.on("pageerror", e => errs.push("pageerror: " + e.message));
page.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text()); });

const t0 = Date.now();
await page.goto("file://" + path.resolve("docs/preview.html"));
await page.waitForFunction("window.__ready === true", null, { timeout: 180000 });
console.log(`boot: ${((Date.now() - t0) / 1000).toFixed(1)}s`);

// a real click through the UI, end to end
const ui = await page.evaluate(async () => {
  const t = performance.now();
  document.querySelector("#kA7").click();
  while (document.getElementById("kA0").disabled) await new Promise(r => setTimeout(r));
  document.querySelector("#kB5").click();
  while (document.getElementById("kA0").disabled) await new Promise(r => setTimeout(r));
  return { ms: Math.round(performance.now() - t),
           says: document.getElementById("tReady").textContent,
           settle: document.getElementById("sSet").textContent,
           ready: document.getElementById("bReady").classList.contains("on"),
           blocks: document.getElementById("sBlk").textContent };
});
console.log("UI round trip, two keypresses:", ui);

// the whole sweep, on the same blocks the buttons drive
const sweep = await page.evaluate(() => {
  const bad = [];
  let worst = 0;
  const t0 = performance.now();
  for (let a = 0; a < 10; a++) for (let b = 0; b < 10; b++) {
    for (const [pad, k] of [["A", a], ["B", b]]) {
      const i = leverIdx[pad + k], t = eng.now;
      eng.setLever(i, true); eng.run(circ.hold);
      eng.setLever(i, false); eng.runUntilStable();
      worst = Math.max(worst, eng.now - t);
    }
    const tens = readDigit("1", true), units = readDigit("0", false);
    const got = (tens === null || units === null) ? null : tens * 10 + units;
    if (got !== a + b) bad.push(`${a}+${b} showed ${got}`);
  }
  return { bad, worst, burned: eng.burned.size,
           secs: ((performance.now() - t0) / 1000).toFixed(1) };
});
console.log(`sweep: ${100 - sweep.bad.length}/100 correct, worst settle ` +
            `${sweep.worst} gt, ${sweep.burned} torches burned, ${sweep.secs}s`);
if (sweep.bad.length) console.log("  " + sweep.bad.slice(0, 5).join("\n  "));

const px = await page.evaluate(() => {
  const c = document.getElementById("cv"), g = c.getContext("webgl");
  const buf = new Uint8Array(c.width * c.height * 4);
  g.readPixels(0, 0, c.width, c.height, g.RGBA, g.UNSIGNED_BYTE, buf);
  const seen = new Set();
  for (let i = 0; i < buf.length; i += 4) seen.add(buf[i] * 65536 + buf[i+1] * 256 + buf[i+2]);
  return { distinctColours: seen.size, w: c.width, h: c.height };
});
console.log("canvas:", px);

for (const which of ["lookDisp", "lookPads", "lookAll"]) {
  await page.click("#" + which, { force: true });
  await page.waitForTimeout(200);
}
await page.evaluate(async () => {
  document.querySelector("#kA9").click();
  while (document.getElementById("kA0").disabled) await new Promise(r => setTimeout(r));
  document.querySelector("#lookDisp").click();
});
await page.waitForTimeout(400);
await page.screenshot({ path: "out/preview_console.png" });
console.log(errs.length ? "ERRORS:\n" + errs.join("\n") : "no page errors");
const ok = !errs.length && !sweep.bad.length && !sweep.burned;
console.log(ok ? "PASS" : "FAIL");
await browser.close();
process.exit(ok ? 0 : 1);
