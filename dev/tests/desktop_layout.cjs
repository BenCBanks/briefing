require('../test_guard.cjs').enforceTestEnvironment();

// Fictional fixture-only desktop layout and keyboard controls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8938';
const shots = process.env.BRIEFING_SHOT_DIR;

async function capture(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, `${name}.png`) });
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, timezoneId: 'America/New_York' });
    await page.addInitScript(() => {
      const state = { spoken: [], paused: false, speaking: false };
      class Utterance { constructor(text) { this.text = text; this.rate = 1; } }
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true });
      Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        get paused() { return state.paused; }, get speaking() { return state.speaking; },
        speak(item) { state.spoken.push(item); state.speaking = true; setTimeout(() => item.onstart?.(), 0); },
        pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; },
        cancel() { state.paused = false; state.speaking = false; },
      } });
      window.__speech = state;
    });
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();

    const shell = await page.evaluate(() => {
      const brand = document.querySelector('.brand').getBoundingClientRect();
      const tabs = document.querySelector('.main-nav').getBoundingClientRect();
      const column = document.querySelector('.today-page').getBoundingClientRect();
      return { brandText: getComputedStyle(document.querySelector('.brand'), '::before').content.replaceAll('"', ''), brandCenter: brand.y + brand.height / 2, tabsCenter: tabs.y + tabs.height / 2,
        tabsVisible: getComputedStyle(document.querySelector('.main-nav')).display !== 'none', tabs: { x: tabs.x, y: tabs.y, width: tabs.width, height: tabs.height },
        columnX: column.x, columnWidth: column.width,
        pageWidth: document.documentElement.scrollWidth };
    });
    assert.equal(await page.locator('.site-header').isVisible(), true, 'Desktop header shares one row with the tabs');
    assert.equal(shell.brandText, 'Briefing');
    assert.ok(shell.tabs.width >= 320 && shell.tabs.height >= 48, `Desktop uses a horizontal top tab bar: ${JSON.stringify(shell.tabs)}`);
    assert.ok(Math.abs(shell.tabsCenter - shell.brandCenter) <= 8, 'Brand and top tabs share a horizontal row');
    assert.ok(shell.columnWidth <= 640, `reading column is at most 640px, got ${shell.columnWidth}`);
    assert.ok(Math.abs(shell.columnX + shell.columnWidth / 2 - 640) <= 1, 'reading column is centered in the desktop viewport');
    assert.equal(shell.pageWidth, 1280, 'desktop page has no horizontal overflow');
    assert.deepEqual(await page.locator('[data-nav]').evaluateAll(items => items.map(item => item.innerText.trim())), ['Today', 'On demand', 'Earlier', 'Topics']);
    await capture(page, 'desktop-today-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'desktop-today-dark');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.setViewportSize({ width: 1024, height: 800 });
    const at1024 = await page.evaluate(() => {
      const nav = document.querySelector('.main-nav').getBoundingClientRect(), column = document.querySelector('.today-page').getBoundingClientRect();
      const links = [...document.querySelectorAll('.main-nav [data-nav]')].map(item => item.getBoundingClientRect());
      return { viewport: innerWidth, page: document.documentElement.scrollWidth, nav, column, links };
    });
    assert.equal(at1024.page, 1024, '1024px desktop does not overflow horizontally');
    assert.ok(at1024.nav.y < 100 && at1024.nav.width >= 320, `All tabs remain in the desktop top bar at 1024px: ${JSON.stringify(at1024.nav)}`);
    assert.ok(at1024.links.every((item,index,all)=>item.x>=0&&item.right<=1024&&(!index||item.x>=all[index-1].right)), 'Four tabs fit in one horizontal row at 1024px');
    assert.ok(at1024.column.width<=640&&Math.abs(at1024.column.x+at1024.column.width/2-512)<=1, '1024px viewport keeps one centered 640px reading column');
    await capture(page, 'desktop-1024-light'); await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'desktop-1024-dark'); await page.emulateMedia({ colorScheme: 'light' });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
    await capture(page, 'phone-today-light'); await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'phone-today-dark'); await page.emulateMedia({ colorScheme: 'light' });
    await page.setViewportSize({ width: 1280, height: 800 });

    await page.keyboard.press('Space');
    await page.waitForFunction(() => window.__speech.spoken.length > 0);
    await page.locator('.mini-player').waitFor();
    const playerBox = await page.locator('.mini-player').boundingBox();
    assert.ok(playerBox.x < 200 && playerBox.width <= 200, 'Player docks in the left rail');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => /Story 2 of/.test(document.querySelector('.mini-player-open small')?.textContent || ''));
    assert.match(await page.locator('.mini-player-open small').innerText(), /Story 2 of/, 'right arrow advances to the next story');
    assert.equal(await page.locator('.mini-player-toggle').getAttribute('aria-keyshortcuts'), 'Space');
    await page.keyboard.press('Space');
    await page.waitForFunction(() => !document.querySelector('.mini-player'));
    assert.match(await page.locator('.today-listen-pill').innerText(), /Resume/);
    await capture(page, 'desktop-player-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'desktop-player-dark');

    await page.emulateMedia({ colorScheme: 'light' });
    await page.setViewportSize({ width: 640, height: 800 }); // CSS viewport at 200% zoom on a 1280 px display.
    await page.waitForTimeout(50);
    const reflow = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth,
      brand: document.querySelector('.brand').getBoundingClientRect(), nav: document.querySelector('.main-nav').getBoundingClientRect() }));
    assert.ok(reflow.scroll <= reflow.width, '200% zoom reflows without horizontal scrolling');
    assert.ok(reflow.nav.y >= reflow.brand.y + reflow.brand.height || reflow.nav.x >= reflow.brand.x + reflow.brand.width,
      'wordmark and navigation do not collide at the reflow width');
    await page.close();
    console.log('Desktop layout: 1280px column, shared top bar/player, Space and chapter arrows, light/dark, and 200% reflow passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
