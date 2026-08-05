// Drive the published demo in a real browser: the JS redstone engine must
// reproduce the Python engine's results, and the page must render.
const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch({
    executablePath: '/opt/pw-browsers/chromium',
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto('file://' + path.resolve(__dirname, '../docs/demo.html'));
  try {
    await page.waitForFunction(
      () => document.getElementById('hBlocks').textContent !== '—',
      null, { timeout: 60000 });
  } catch (e) {
    console.log('page never finished loading a circuit');
    console.log('overlay:', await page.evaluate(
      () => document.getElementById('ov').textContent));
    errors.forEach(x => console.log('  ' + x));
    await browser.close();
    process.exit(1);
  }
  await page.waitForTimeout(1500);

  const header = await page.evaluate(() => ({
    blocks: document.getElementById('hBlocks').textContent,
    gates: document.getElementById('hGates').textContent,
    depth: document.getElementById('hDepth').textContent,
    drawn: document.getElementById('lyN').textContent,
    desc: document.getElementById('desc').textContent,
  }));
  console.log('loaded:', JSON.stringify(header));

  // exercise the ALU through the UI: set A=13, B=6, run each op, read the DOM
  const OPS = ['ADD', 'SUB', 'AND', 'OR', 'XOR', 'NOT', 'SHL', 'SHR'];
  const results = [];
  for (let k = 0; k < 8; k++) {
    const ok = await page.evaluate(async (k) => {
      op = k; operands.A = 13; operands.B = 6;
      setLevers();
      lastTicks = eng.runUntilStable();
      refreshColors(false); updateReadout();
      return {
        got: +document.getElementById('rDec').textContent,
        exp: +document.getElementById('rExp').textContent,
        ticks: lastTicks,
      };
    }, k);
    results.push([OPS[k], ok]);
  }
  let bad = 0;
  for (const [name, r] of results) {
    const pass = r.got === r.exp;
    if (!pass) bad++;
    console.log(`  ${name.padEnd(4)} 13,6 -> ${r.got} (expect ${r.exp}) ${r.ticks} gt  ${pass ? 'OK' : 'WRONG'}`);
  }

  // run the built-in verification sweep
  await page.click('#bVerify');
  await page.waitForFunction(
    () => !document.getElementById('bVerify').disabled, null, { timeout: 300000 });
  const verdict = await page.evaluate(() => ({
    text: document.getElementById('verdict').textContent,
    cls: document.getElementById('verdict').className,
  }));
  console.log('verify:', verdict.text);

  // the canvas must actually have drawn something
  const painted = await page.evaluate(() => {
    const c = document.getElementById('cv');
    const gl = c.getContext('webgl');
    const px = new Uint8Array(4 * 64 * 64);
    gl.readPixels(Math.floor(c.width / 2) - 32, Math.floor(c.height / 2) - 32,
      64, 64, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const uniq = new Set();
    for (let i = 0; i < px.length; i += 4) uniq.add(px[i] + ',' + px[i + 1] + ',' + px[i + 2]);
    return { colors: uniq.size, w: c.width, h: c.height };
  });
  console.log('canvas:', JSON.stringify(painted));

  await page.screenshot({ path: path.resolve(__dirname, '../out/demo.png') });
  await page.close();

  // --- and on a phone, where this page had every trap the preview page fixed
  // One `drag` record meant a second finger overwrote the first, so there was
  // no pinch at all — and `touch-action:none` on a canvas taking 56% of the
  // height meant a thumb landing on the machine could neither zoom it nor
  // scroll past it. Both are rewritten; neither was checked by anything.
  const { devices } = require('playwright');
  const m = await browser.newPage({ ...devices['iPhone 13'] });
  m.on('pageerror', e => errors.push('mobile pageerror: ' + e.message));
  await m.goto('file://' + path.resolve(__dirname, '../docs/demo.html'));
  await m.waitForFunction(
    () => document.getElementById('hBlocks').textContent !== '—',
    null, { timeout: 120000 });
  await m.waitForTimeout(600);

  const touch = await m.evaluate(async () => {
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const cv = document.getElementById('cv');
    const r = cv.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const ev = (t, id, cx, cy, primary) => cv.dispatchEvent(new PointerEvent(t,
      { pointerId: id, pointerType: 'touch', isPrimary: !!primary,
        bubbles: true, clientX: cx, clientY: cy,
        buttons: t === 'pointerup' ? 0 : 1 }));

    // the view must be bounded in pixels, not only in viewport units
    const viewH = Math.round(document.getElementById('view')
      .getBoundingClientRect().height);

    // one finger, sideways: turns the machine, leaves the page alone
    const az0 = cam.az, sy0 = scrollY;
    ev('pointerdown', 1, x, y, true);
    for (let i = 1; i <= 10; i++) ev('pointermove', 1, x + i * 12, y, true);
    ev('pointerup', 1, x + 120, y, true);
    const turned = Math.abs(cam.az - az0), scrolledWhileTurning = scrollY - sy0;

    // one finger, straight up: reads on, and does not tilt
    const el0 = cam.el, sy1 = scrollY;
    ev('pointerdown', 2, x, y, true);
    for (let i = 1; i <= 12; i++) ev('pointermove', 2, x, y - i * 14, true);
    ev('pointerup', 2, x, y - 168, true);
    await wait(150);
    const scrolled = scrollY - sy1, tilted = Math.abs(cam.el - el0);
    scrollTo({ top: sy1, behavior: 'instant' });
    await wait(100);

    // two fingers: pinch, which this page did not have at all
    const d0 = cam.dist;
    ev('pointerdown', 3, x - 40, y, true);
    ev('pointerdown', 4, x + 40, y, false);
    ev('pointermove', 3, x - 110, y, true);
    ev('pointermove', 4, x + 110, y, false);
    const spread = cam.dist / d0;
    ev('pointermove', 3, x - 20, y, true);
    ev('pointermove', 4, x + 20, y, false);
    const closed = cam.dist / d0;
    ev('pointerup', 3, x - 20, y, true);
    ev('pointerup', 4, x + 20, y, false);
    return { viewH, vh: innerHeight, turned: +turned.toFixed(3),
             scrolledWhileTurning, scrolled, tilted: +tilted.toFixed(3),
             spread: +spread.toFixed(2), closed: +closed.toFixed(2) };
  });

  const touchBad = [];
  if (!(touch.turned > 0.05)) touchBad.push(`a sideways drag turned ${touch.turned} rad`);
  if (touch.scrolledWhileTurning !== 0)
    touchBad.push(`turning scrolled the page ${touch.scrolledWhileTurning}px`);
  if (!(touch.scrolled > 80))
    touchBad.push(`a swipe up the canvas scrolled ${touch.scrolled}px — the ` +
                  `machine is a dead zone a thumb cannot get past`);
  if (touch.tilted !== 0) touchBad.push(`that swipe also tilted by ${touch.tilted} rad`);
  if (!(touch.spread < 0.9)) touchBad.push(`spreading two fingers gave x${touch.spread}`);
  if (!(touch.closed > 1.1)) touchBad.push(`pinching together gave x${touch.closed}`);
  if (touch.viewH > 520)
    touchBad.push(`the view is ${touch.viewH}px in a ${touch.vh}px screen — ` +
                  `a viewport-unit height with no pixel cap runs away in a frame`);
  console.log(touchBad.length
    ? 'MOBILE:\n  ' + touchBad.join('\n  ')
    : `mobile: one finger turns (${touch.turned} rad, page still), a swipe ` +
      `reads on (${touch.scrolled}px, camera still), pinch zooms ` +
      `(x${touch.spread} / x${touch.closed}), view ${touch.viewH}px capped`);

  if (errors.length) { console.log('JS ERRORS:'); errors.slice(0, 8).forEach(e => console.log('  ' + e)); }
  await browser.close();
  const fail = bad > 0 || !verdict.cls.includes('ok') || painted.colors < 5
    || touchBad.length || errors.length;
  console.log(fail ? 'BROWSER CHECK FAILED' : 'BROWSER CHECK PASSED');
  process.exit(fail ? 1 : 0);
})();
