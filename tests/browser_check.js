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

  if (errors.length) { console.log('JS ERRORS:'); errors.slice(0, 8).forEach(e => console.log('  ' + e)); }
  await browser.close();
  const fail = bad > 0 || !verdict.cls.includes('ok') || painted.colors < 5 || errors.length;
  console.log(fail ? 'BROWSER CHECK FAILED' : 'BROWSER CHECK PASSED');
  process.exit(fail ? 1 : 0);
})();
