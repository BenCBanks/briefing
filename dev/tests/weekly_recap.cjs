require('../test_guard.cjs').enforceTestEnvironment();

// Weekly recap interaction contract; all edition data and API responses are fictional fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8964';
const shots = process.env.BRIEFING_SHOT_DIR;
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/weekly-recap.json'), 'utf8'));
const staticEditions = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'));
for (const topicId of ['energy', 'local-news', 'sports-business']) {
  assert.ok(fixture.coverage.some(item => item.topic_id === topicId), `weekly fixture includes restored ${topicId} topic`);
}
const weeklyPack = {
  editions: [
    ...staticEditions.editions.filter(item => item.kind !== 'weekly'),
    fixture,
  ],
};

async function setup(page) {
  await page.route('**/api/profile', route => route.fulfill({ status: 404, json: {} }));
  await page.route('**/api/edition**', route => route.fulfill({ status: 200, json: { generated: false, sources: [] } }));
  await page.route('**/api/episodes', route => route.fulfill({ status: 200, json: { jobs: [], left_today: 3 } }));
  await page.route('**/content/editions.json', route => route.fulfill({ status: 200, json: weeklyPack }));
}

async function screenshot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, `${name}.png`) });
}

async function pageFor(browser, width = 390, height = 844) {
  const page = await browser.newPage({ viewport: { width, height }, timezoneId: 'America/New_York' });
  // Keep listening fully deterministic and local; no audio provider or device voice runs.
  await page.addInitScript(() => {
    const state = { speaking: false, paused: false };
    class Utterance { constructor(text) { this.text = text; this.rate = 1; } }
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true });
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      get speaking() { return state.speaking; }, get paused() { return state.paused; },
      speak(item) { state.speaking = true; setTimeout(() => item.onstart?.(), 0); },
      pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; },
      cancel() { state.speaking = false; state.paused = false; },
    } });
  });
  await setup(page);
  return page;
}

async function verifyEarlierAndRecap(page, prefix) {
  await page.goto(`${baseURL}/#library`, { waitUntil: 'domcontentloaded' });
  const card = page.getByRole('link', { name: /Weekly recap 8-14 Nov · \d+ stories · \d+ min read/ });
  await card.waitFor();
  assert.equal(await page.locator('.earlier-weekly-card').count(), 1, 'only the latest recap gets the featured card');
  assert.match(await page.locator('.earlier-weekly-card').innerText(), /\d+ stories · \d+ min read/, 'featured recap labels its reading time clearly');
  assert.match(await page.locator('.earlier-weekly-card .library-date-meta').innerText(), /^8-14 Nov · \d+ stories · \d+ min read$/, 'date range stays on one supporting line with reading time');
  assert.equal(await page.locator('.earlier-weekly-card-action').innerText(), 'Open recap →');
  const cardStyle = await page.locator('.earlier-weekly-card').evaluate(el => ({ height: el.getBoundingClientRect().height, columns: getComputedStyle(el).gridTemplateColumns, action: el.querySelector('.earlier-weekly-card-action').getBoundingClientRect().height }));
  assert.ok(cardStyle.height >= 78 && cardStyle.action >= 44, `Featured card has clear two-line hierarchy and a 44px action: ${JSON.stringify(cardStyle)}`);
  assert.ok(await page.locator('.library-briefing-list li').count() > 0, 'Editions segment lists dated daily briefings');
  assert.match(await page.locator('.library-briefing-list li').first().innerText(), /\d+ stories · \d+ min read/, 'daily briefing date includes its story count and reading time');
  assert.equal(await page.getByText('Clarity before commitment', { exact: true }).count(), 0, 'latest recap is not repeated in the lower archive list');
  if (page.viewportSize().width <= 400) {
    const titleHeight = await page.locator('.earlier-weekly-card strong').evaluate(node => node.getBoundingClientRect().height);
    assert.ok(titleHeight <= 44, `phone card title stays within two lines (${titleHeight}px)`);
  }
  await screenshot(page, `${prefix}-earlier-light`);
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, `${prefix}-earlier-dark`);
  await page.emulateMedia({ colorScheme: 'light' });
  await card.click();
  await page.locator('.weekly-recap-page').waitFor();
  assert.equal(new URL(page.url()).hash, '#edition/weekly/weekly-fixture-2026-11-08');
  assert.match(await page.locator('.weekly-listen').innerText(), /Listen\s*·\s*about (9|10) min/,
    'weekly recap shows its honest finite listening length beside the primary action');
  await page.getByRole('status').getByText('This recap brings together the developments selected for your topics.').waitFor();
  await page.getByText('Next recap expected Sunday, November 22.').waitFor();
  assert.equal(await page.locator('.weekly-key-developments li button').count(), 3);
  assert.equal(await page.locator('.weekly-recap-sources li button').count(), 3);
  await screenshot(page, `${prefix}-recap-light`);
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, `${prefix}-recap-dark`);
  await page.emulateMedia({ colorScheme: 'light' });

  if (page.viewportSize().width <= 400) {
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    const layout = await page.evaluate(() => ({
      viewport: innerWidth,
      page: document.documentElement.scrollWidth,
      controls: [...document.querySelectorAll('.weekly-recap-page button')].map(button => ({
        name: button.innerText.trim(), width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height,
      })),
    }));
    assert.ok(layout.page <= layout.viewport, `weekly recap reflows at 200% text without horizontal overflow: ${JSON.stringify(layout)}`);
    assert.ok(layout.controls.every(control => control.width >= 44 && control.height >= 44),
      `weekly recap controls remain at least 44px: ${JSON.stringify(layout.controls)}`);
    await page.locator('.weekly-listen').evaluate(button => button.focus({ focusVisible: true }));
    const focus = await page.locator('.weekly-listen').evaluate(button => ({
      visible: button.matches(':focus-visible'), outline: getComputedStyle(button).outlineStyle,
      width: getComputedStyle(button).outlineWidth,
    }));
    assert.ok(focus.visible && focus.outline !== 'none' && parseFloat(focus.width) >= 2,
      `weekly listening action has a visible focus ring: ${JSON.stringify(focus)}`);
    await screenshot(page, `${prefix}-recap-large-light`);
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, `${prefix}-recap-large-dark`);
    await page.locator('.weekly-recap-actions').scrollIntoViewIfNeeded();
    await screenshot(page, `${prefix}-recap-ending-large-dark`);
    await page.emulateMedia({ colorScheme: 'light' });
    await screenshot(page, `${prefix}-recap-ending-large-light`);
    await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));

    await page.locator('.weekly-listen').click();
    await page.waitForFunction(() => document.querySelector('.weekly-listen')?.getAttribute('aria-label')?.startsWith('Pause'));
    await page.locator('.mini-player-open').click();
    await page.locator('.player-dialog').waitFor({ state: 'visible' });
    assert.match(await page.locator('[data-player-now-length]').innerText(), /^9 min left$/,
      'the player length agrees with the recap listening estimate at 1×');
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    const playerLayout = await page.evaluate(() => ({
      viewport: innerWidth,
      page: document.documentElement.scrollWidth,
      sheet: document.querySelector('.player-sheet')?.getBoundingClientRect().width,
      controls: [...document.querySelectorAll('.player-dialog button, .player-dialog select')].map(control => ({
        name: control.innerText.trim(), width: control.getBoundingClientRect().width, height: control.getBoundingClientRect().height,
      })),
    }));
    assert.ok(playerLayout.page <= playerLayout.viewport && playerLayout.sheet <= playerLayout.viewport,
      `weekly player fits the phone at 200% text: ${JSON.stringify(playerLayout)}`);
    assert.ok(playerLayout.controls.filter(control => control.width && control.height).every(control => control.width >= 44 && control.height >= 44),
      `weekly player controls remain usable at 200% text: ${JSON.stringify(playerLayout.controls)}`);
    await screenshot(page, `${prefix}-player-large-light`);
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, `${prefix}-player-large-dark`);
    await page.emulateMedia({ colorScheme: 'light' });
    await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
    await page.locator('.player-dialog [data-player-toggle]').evaluate(button => button.focus({ focusVisible: true }));
    const playerFocus = await page.locator('.player-dialog [data-player-toggle]').evaluate(button => ({
      visible: button.matches(':focus-visible'), outline: getComputedStyle(button).outlineStyle,
      width: getComputedStyle(button).outlineWidth,
    }));
    assert.ok(playerFocus.visible && playerFocus.outline !== 'none' && parseFloat(playerFocus.width) >= 2,
      `weekly player has a visible keyboard focus ring: ${JSON.stringify(playerFocus)}`);
    await page.locator('.player-sheet-top button').click();
  }

  await page.locator('.weekly-key-developments li button').first().click();
  await page.locator('.edition[data-edition="daily-sample-001"] h1').waitFor();
  assert.equal(new URL(page.url()).hash, '#edition/daily/daily-sample-001/daily-payments-001');
  await page.getByRole('button', { name: '‹ Weekly recap' }).click();
  await page.waitForFunction(() => location.hash === '#edition/weekly/weekly-fixture-2026-11-08');
  await page.locator('.weekly-recap-page').waitFor();
  await page.locator('.weekly-recap-sources li button').first().click();
  await page.waitForFunction(() => location.hash === '#source/fed-payments');
  await page.locator('.source-page h1, main h1').first().waitFor();
  assert.equal(new URL(page.url()).hash, '#source/fed-payments');
  await page.locator('.reader-back').filter({ hasText: 'Weekly recap' }).waitFor();
  const sourceBackLabel = (await page.locator('.reader-back').innerText()).trim();
  assert.equal(sourceBackLabel, 'Weekly recap', `${prefix} source route keeps its recap parent label`);
  await page.locator('.reader-back').click();
  await page.locator('.weekly-recap-page').waitFor();
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const phone = await pageFor(browser);
    await verifyEarlierAndRecap(phone, 'weekly-phone');
    const desktop = await pageFor(browser, 1280, 800);
    await verifyEarlierAndRecap(desktop, 'weekly-desktop');
    const desktopWidths = await desktop.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth, recap: document.querySelector('.weekly-recap-page')?.getBoundingClientRect().width }));
    assert.ok(desktopWidths.page <= desktopWidths.viewport, `desktop page reflows without horizontal overflow: ${JSON.stringify(desktopWidths)}`);
    assert.ok(desktopWidths.recap <= 680, `recap remains a readable column: ${desktopWidths.recap}px`);
    console.log('Weekly recap: featured Earlier card, date range, thin-week message, original-story links, citations, next availability and responsive light/dark views passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
