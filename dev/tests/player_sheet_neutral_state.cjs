require('../test_guard.cjs').enforceTestEnvironment();

// Fictional edition fixture: verify the phone player and neutral selected speed state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8945';
const shotDir = process.env.BRIEFING_SHOT_DIR;

async function screenshot(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ path: path.join(shotDir, `${name}.png`), animations: 'disabled' });
}

async function assertNeutralSpeed(page) {
  const states = await page.locator('.player-speeds button').evaluateAll(buttons => buttons.map(button => {
    const style = getComputedStyle(button);
    const rect = button.getBoundingClientRect();
    return { pressed: button.getAttribute('aria-pressed'), color: style.color, background: style.backgroundColor,
      border: style.borderColor, decoration: style.textDecorationLine, y: rect.y, height: rect.height };
  }));
  const selected = states.find(state => state.pressed === 'true');
  const idle = states.find(state => state.pressed === 'false');
  assert.ok(selected && idle, 'one speed is selected and the other options remain available');
  assert.deepEqual([selected.color, selected.background, selected.border], [idle.color, idle.background, idle.border], 'pressed speed changes no colors');
  assert.equal(selected.decoration, 'underline', 'an underline marks the selected speed without changing color');
  assert.ok(states.every(state => state.height >= 44) && new Set(states.map(state => state.y)).size === 1, 'five speed controls fit one row with 44px targets');
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light', timezoneId: 'America/New_York' });
    await page.addInitScript(() => {
      const state = { paused: false, speaking: false };
      class Utterance { constructor(text) { this.text = text; this.rate = 1; } }
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { configurable: true, value: Utterance });
      Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        getVoices: () => [{ name: 'Natural English', lang: 'en-US', voiceURI: 'fixture-natural', default: true }],
        get paused() { return state.paused; }, get speaking() { return state.speaking; },
        speak(item) { state.speaking = true; setTimeout(() => item.onstart?.(), 0); },
        pause() { state.paused = true; }, resume() { state.paused = false; }, cancel() { state.paused = false; state.speaking = false; },
      } });
    });
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-listen-pill').waitFor({ state: 'visible' });
    await page.locator('.today-listen-pill').click();
    await page.locator('.mini-player').waitFor({ state: 'visible' });
    assert.match(await page.locator('.mini-player-open small').innerText(), /^Story \d+ of \d+ · \d+ min left$/, 'mini player shows estimated remaining listening time');
    await page.locator('.mini-player-open').click();
    const dialog = page.locator('.player-dialog');
    await dialog.waitFor({ state: 'visible' });
    assert.match(await dialog.locator('[data-player-now-length]').innerText(), /^\d+ min left$/, 'full player shows remaining listening time');
    assert.equal(await dialog.locator('[data-player-skip="-15"]').innerText(), '−15');
    assert.equal(await dialog.locator('[data-player-skip="30"]').innerText(), '+30');
    assert.equal(await dialog.locator('[data-player-speed]').count(), 5);
    await assertNeutralSpeed(dialog);
    assert.ok(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth), 'player sheet has no horizontal overflow');
    assert.ok(await dialog.locator('[data-player-seek]').evaluate(input => input.getBoundingClientRect().width >= 320), 'scrubber spans the phone sheet');
    await screenshot(page, 'player-main-light-390');
    await page.emulateMedia({ colorScheme: 'dark' });
    await assertNeutralSpeed(dialog);
    await screenshot(page, 'player-main-dark-390');
    await page.emulateMedia({ colorScheme: 'light' });
    await dialog.getByRole('button', { name: 'Chapters' }).click();
    assert.ok(await dialog.locator('.player-chapter-row').count() >= 3, 'chapter view lists each fixture story');
    await screenshot(page, 'player-chapters-light-390');
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, 'player-chapters-dark-390');
    console.log('Player sheet: chapters, scrubber, −15/+30, five neutral speed options, and 390px layout passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
