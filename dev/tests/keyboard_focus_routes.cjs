require('../test_guard.cjs').enforceTestEnvironment();

// Keyboard flow checks across reader routes and the app's modal sheets.
// Uses bundled fictional content and stubs browser speech; no provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const episodes = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episodes.json'), 'utf8')).episodes;
const routeCases = [
  ['today', '#today'], ['earlier', '#library'], ['topics', '#topics'],
  ['settings', '#preferences'], ['interests', '#interests'], ['ask', '#ask-episode'],
  ['weekly', '#weekly'], ['story', '#edition/daily/daily-sample-001/daily-payments-001'],
  ['explanation', '#explanation/dive-payments-basics'], ['source', '#source/fed-payments'],
  ['corrections', '#corrections'], ['episode', `#episode/${episodes[0].id}`],
  ['episode deck', '#episodes'],
];

async function newPage(browser, { theme = 'light', width = 390, height = 844 } = {}) {
  const page = await browser.newPage({ viewport: { width, height }, colorScheme: theme, timezoneId: 'America/New_York' });
  await page.setDefaultTimeout(9000);
  await page.addInitScript(() => {
    class StubUtterance { constructor(text) { this.text = text; this.rate = 1; } }
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { configurable: true, value: StubUtterance });
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      getVoices: () => [], get paused() { return false; }, get speaking() { return false; },
      speak: utterance => setTimeout(() => utterance.onstart?.(), 0), pause() {}, resume() {}, cancel() {},
    } });
  });
  for (const route of ['/api/profile', '/api/generate', '/api/edition', '/api/corrections', '/api/status', '/api/voice']) {
    await page.route(`**${route}**`, request => request.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
  }
  await page.route('**/api/episodes**', request => request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ jobs: [] }) }));
  await page.route('**/dev/tests/fixtures/episodes.json', request => request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ episodes }) }));
  return page;
}

async function visit(browser, route, theme = 'light') {
  const page = await newPage(browser, { theme });
  const query = route.includes('episode') ? '?episodes=1' : '';
  await page.goto(`${baseURL}/${query}${route}`, { waitUntil: 'domcontentloaded' });
  const ready = route.startsWith('#episode/') ? '.episode-page h1'
    : route === '#episodes' ? '.episode-deck-page'
      : route === '#topics' ? '.topics-page h1'
        : route === '#library' ? '.library-page h1, main > h1'
          : route === '#ask-episode' ? '.episode-request-page h1'
            : route.startsWith('#source/') ? '.source-page h1, main > h1'
              : route.startsWith('#explanation/') ? '.deep-dive-page h1, main > h1'
                : route === '#weekly' || route.startsWith('#edition/') ? 'main h1' : 'main h1';
  await page.locator(ready).first().waitFor();
  return page;
}

async function assertKeyboardFocus(page, label) {
  await page.keyboard.press('Tab');
  const first = await page.evaluate(() => ({
    tag: document.activeElement?.tagName,
    className: String(document.activeElement?.className || ''),
    visible: Boolean(document.activeElement?.getClientRects().length),
    outline: getComputedStyle(document.activeElement).outlineStyle,
    outlineWidth: getComputedStyle(document.activeElement).outlineWidth,
  }));
  if (['today', 'topics', 'library'].some(hash => page.url().includes(`#${hash}`))) {
    assert.match(first.className, /skip/, `${label}: first Tab reaches Skip to content`);
  }
  assert.ok(first.visible, `${label}: focus stays on a visible element`);
  assert.notEqual(first.outline, 'none', `${label}: first keyboard target has a visible focus outline`);
  assert.ok(parseFloat(first.outlineWidth) >= 2, `${label}: focus outline is at least 2px`);
  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press('Tab');
    const state = await page.evaluate(() => ({
      visible: Boolean(document.activeElement?.getClientRects().length),
      tag: document.activeElement?.tagName,
      inDialog: Boolean(document.activeElement?.closest('dialog[open], [aria-modal="true"]')),
      outline: getComputedStyle(document.activeElement).outlineStyle,
      outlineWidth: getComputedStyle(document.activeElement).outlineWidth,
    }));
    // Reaching BODY at the end of the document is the normal browser exit point.
    if (state.tag === 'BODY') break;
    assert.ok(state.visible, `${label}: Tab ${index + 2} remains visible`);
    assert.notEqual(state.outline, 'none', `${label}: Tab ${index + 2} has a visible focus style`);
    assert.ok(parseFloat(state.outlineWidth) >= 2, `${label}: Tab ${index + 2} focus outline is at least 2px`);
    if (state.inDialog) assert.ok(await page.locator('dialog[open], [aria-modal="true"]').count(), `${label}: modal focus is contained by its dialog`);
  }
}

async function assertTabAndStoryOrder(page, label, story = false) {
  const expected = [
    { name: 'Skip to content', selector: '.skip' },
    { name: 'Today tab', selector: '[data-nav="today"]' },
    { name: 'On demand tab', selector: '[data-nav="on-demand"]' },
    { name: 'Earlier tab', selector: '[data-nav="library"]' },
    { name: 'Topics tab', selector: '[data-nav="topics"]' },
    { name: story ? 'Story back to Today' : 'Today Listen action', selector: story ? '.reader-back' : '.today-listen-pill' },
  ];
  for (const item of expected) {
    await page.keyboard.press('Tab');
    const focus = await page.evaluate(selector => {
      const style = getComputedStyle(document.activeElement);
      return { matches: document.activeElement.matches(selector), visible: Boolean(document.activeElement.getClientRects().length), outline: style.outlineStyle, width: parseFloat(style.outlineWidth), color: style.outlineColor };
    }, item.selector);
    assert.ok(focus.matches, `${label}: ${item.name} is next in keyboard order`);
    assert.ok(focus.visible, `${label}: ${item.name} remains visible`);
    assert.notEqual(focus.outline, 'none', `${label}: ${item.name} has a focus-visible ring`);
    assert.ok(focus.width >= 2, `${label}: ${item.name} ring is at least 2px`);
    assert.notEqual(focus.color, 'rgba(0, 0, 0, 0)', `${label}: ${item.name} ring has a visible color`);
  }
}

async function assertEscapeReturns(page, opener, dialog, label) {
  await dialog.waitFor({ state: 'visible' });
  assert.ok(await dialog.evaluate(el => el.contains(document.activeElement)), `${label}: opening moves focus into the sheet`);
  for (let index = 0; index < 18; index += 1) {
    await page.keyboard.press(index % 2 ? 'Shift+Tab' : 'Tab');
    const focus = await dialog.evaluate(el => ({
      contained: el.contains(document.activeElement),
      visible: Boolean(document.activeElement?.getClientRects().length),
      outline: getComputedStyle(document.activeElement).outlineStyle,
      outlineWidth: getComputedStyle(document.activeElement).outlineWidth,
    }));
    assert.ok(focus.contained, `${label}: keyboard focus remains inside the modal while open`);
    assert.ok(focus.visible, `${label}: modal focus target stays visible`);
    assert.notEqual(focus.outline, 'none', `${label}: modal keyboard focus has a visible ring`);
    assert.ok(parseFloat(focus.outlineWidth) >= 2, `${label}: modal focus ring is at least 2px`);
  }
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'detached' });
  await page.waitForFunction(selector => document.activeElement?.matches(selector), opener);
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    for (const theme of ['light', 'dark']) {
      for (const [label, route] of routeCases) {
        const page = await visit(browser, route, theme);
        if (route === '#today' || route.startsWith('#edition/')) {
          const orderPage = await visit(browser, route, theme);
          await assertTabAndStoryOrder(orderPage, `${label}/${theme}`, route.startsWith('#edition/'));
          await orderPage.close();
        }
        await assertKeyboardFocus(page, `${label}/${theme}`);
        await page.close();
      }
    }

    const ask = await visit(browser, '#ask-episode');
    const length = ask.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: '10 min' });
    await length.focus();
    await ask.keyboard.press('Enter');
    assert.equal(await length.getAttribute('aria-pressed'), 'true', 'Visible length buttons are keyboard selectable');
    await ask.keyboard.press('Escape');
    assert.equal(await ask.locator('.episode-style-dialog[open]').count(), 0, 'Visible length choices do not open a modal sheet');
    await ask.close();

    const today = await visit(browser, '#today');
    await today.locator('.today-contents [data-read-section]').first().click();
    await today.locator('.edition-section').first().waitFor();
    const why = today.locator('.why-story-trigger').first();
    await why.waitFor(); await why.click();
    await assertEscapeReturns(today, '.why-story-trigger', today.locator('.why-story-dialog[open]'), 'Why this story sheet');
    await why.click();
    await today.getByRole('button', { name: 'Not interested in this topic' }).click();
    assert.ok(await today.locator('.why-story-confirm [aria-label="Confirm mute"], .why-story-confirm button').first().evaluate(el => el === document.activeElement), 'Confirmation moves focus to its first action');
    await today.keyboard.press('Escape');
    await today.locator('.why-story-dialog').waitFor({ state: 'detached' });
    assert.equal(await today.evaluate(() => document.activeElement?.classList.contains('why-story-trigger')), true, 'Escape from confirmation returns to the Why opener');
    await today.close();

    console.log('Keyboard focus audit passed across 13 routes in light/dark and verified modal traversal, Escape, and focus return.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
