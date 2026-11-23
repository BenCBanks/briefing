require('../test_guard.cjs').enforceTestEnvironment();

// Mobile fixed bars keep clear of device safe areas and the software keyboard.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8934';
const shotDir = process.env.BRIEFING_SHOT_DIR;

async function capture(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ animations: 'disabled', path: path.join(shotDir, `${name}.png`) });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.addInitScript(() => {
      const listeners = new Map();
      const visualViewportMock = {
        height: window.innerHeight,
        offsetTop: 0,
        addEventListener(type, listener) {
          const rows = listeners.get(type) || [];
          rows.push(listener);
          listeners.set(type, rows);
        },
        emit(type) { for (const listener of listeners.get(type) || []) listener(new Event(type)); },
      };
      Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => visualViewportMock });
      window.__setKeyboardViewport = height => {
        visualViewportMock.height = height;
        visualViewportMock.emit('resize');
      };
    });
    await page.route('**/api/episodes', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ jobs: [], left_today: 3 }) }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();

    assert.match(await page.locator('meta[name="viewport"]').getAttribute('content'), /viewport-fit=cover/, 'The layout can use iPhone safe-area insets');
    const nav = page.locator('.main-nav');
    const navBox = await nav.boundingBox();
    assert.ok(navBox.height >= 56 && Math.abs(navBox.y + navBox.height - 844) < 1, 'Bottom navigation reaches the device edge with its safe-area padding inside');
    assert.ok(parseFloat(await page.locator('main').evaluate(el => getComputedStyle(el).paddingBottom)) >= navBox.height, 'Page content clears the fixed bottom navigation');

    await page.locator('[data-nav="on-demand"]').click();
    const prompt = page.locator('#episode-request-prompt');
    await prompt.focus();
    await page.evaluate(() => {
      const player = document.createElement('div');
      player.className = 'mini-player';
      player.innerHTML = '<button class="mini-player-back" aria-label="Back">‹</button><span class="mini-player-open"><strong>Fictional daily briefing</strong><small>Story 1 of 3</small></span><button class="mini-player-toggle" aria-label="Pause">Ⅱ</button>';
      document.querySelector('#player-root').append(player);
      return Promise.all(document.getAnimations().map(animation => animation.finished));
    });
    const player = page.locator('.mini-player');
    const defaultPlayerBox = await player.boundingBox();
    assert.ok(defaultPlayerBox.y + defaultPlayerBox.height <= (await nav.boundingBox()).y, `The mini player sits above the bottom navigation: player=${JSON.stringify(defaultPlayerBox)} nav=${JSON.stringify(await nav.boundingBox())}`);
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme });
      await capture(page, `sticky-normal-${theme}`);
    }
    await page.evaluate(() => window.__setKeyboardViewport(500));
    await page.waitForFunction(() => document.body.classList.contains('keyboard-open'));
    assert.equal(await nav.evaluate(el => getComputedStyle(el).display), 'none', 'Bottom navigation clears the software keyboard');
    const playerBox = await player.boundingBox();
    assert.ok(playerBox.y + playerBox.height <= 501, `Mini player stays above the keyboard: ${JSON.stringify(playerBox)}`);
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme });
      await capture(page, `sticky-keyboard-${theme}`);
    }

    await page.evaluate(() => window.__setKeyboardViewport(844));
    await page.waitForFunction(() => !document.body.classList.contains('keyboard-open'));
    assert.notEqual(await nav.evaluate(el => getComputedStyle(el).display), 'none', 'Navigation returns when the keyboard closes');
    assert.ok((await player.boundingBox()).y + (await player.boundingBox()).height <= (await nav.boundingBox()).y, 'Mini player returns to its normal stacked position');

    await prompt.blur();
    await prompt.focus();
    assert.notEqual(await nav.evaluate(el => getComputedStyle(el).display), 'none', 'A focused field with no virtual keyboard keeps navigation available');
    console.log('Sticky bars clear the safe area, content, and virtual keyboard at 390px.');
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
