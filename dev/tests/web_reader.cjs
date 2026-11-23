require('../test_guard.cjs').enforceTestEnvironment();

// Run with Playwright installed and BRIEFING_URL pointing at a private preview server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8898';

function fixtureWav(seconds = 8) {
  const samples = seconds * 22050;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(22050, 24); wav.writeUInt32LE(44100, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(3000 * Math.sin(2 * Math.PI * 440 * i / 22050)), 44 + i * 2);
  return wav;
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.today-listen-pill').count(), 1, 'Today has a Listen control when speech is available');
    assert.equal(await page.locator('.prototype-voice-label').count(), 0, 'No prototype-voice wording is exposed');
    assert.equal(await page.locator('.mini-player').count(), 0, 'Idle has only the inline Listen pill');
    const layout = await page.evaluate(() => ({
      articleTop: document.querySelector('.today-page').getBoundingClientRect().top,
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    assert.ok(layout.articleTop < 500, 'Article should begin in the first phone screen');
    assert.equal(layout.overflow, false);
    await page.locator('.today-contents [data-read-section]').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.edition-section').count(), 1, 'Reader routes show one story at a time');
    assert.equal(await page.locator('.story-progress').innerText(), 'Story 1 of 4');
    await page.locator('[data-source]').first().click();
    await page.locator('.source-title').waitFor();
    assert.equal(await page.getByRole('link', { name: /Open original/ }).count(), 1,
                 'External references keep their original-source link');
    assert.equal(await page.getByRole('heading', { name: 'What this supports' }).count(), 1,
                 'Source page explains what the reference supports');
    await page.locator('.reader-back').click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.match(new URL(page.url()).hash, /daily-sample-001\/daily-payments-001/);
    for (let index = 1; index < 4; index += 1) await page.locator('.next-story').click();
    await page.locator('.up-to-date').waitFor();
    await page.locator('[data-finish]').scrollIntoViewIfNeeded();
    assert.equal(await page.locator('.main-nav').isVisible(), true, 'V3 keeps labelled navigation available');
    await page.locator('[data-finish]').click();
    assert.equal(await page.locator('[data-finish]').textContent(), 'Finished');
    const save = page.locator('[data-save-edition]');
    await save.scrollIntoViewIfNeeded();
    const beforeSave = await page.evaluate(() => ({ scrollY, reading: JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress }));
    await save.click();
    assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-save-edition')), true,
                 'Saving keeps keyboard focus on the same control');
    assert.ok(Math.abs((await page.evaluate(() => scrollY)) - beforeSave.scrollY) < 2,
              'Saving at the article end keeps the reading position');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress), beforeSave.reading,
                     'Saving does not replace the reading anchor');
    assert.deepEqual(errors, []);
    await page.close();

    const recorded = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await recorded.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    const audio = fixtureWav(20);
    let chapterTitles = [];
    let chapterIds = [];
    const playbackErrors = [];
    recorded.on('pageerror', error => playbackErrors.push(error.message));
    await recorded.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const pack = await response.json();
      const daily = pack.editions.find(item => item.kind === 'daily');
      chapterTitles = daily.sections.map(section => section.heading);
      chapterIds = daily.sections.map(section => section.id);
      daily.audio = { state: 'recorded', revision_id: daily.revision_id, script_hash: daily.script_hash, url: '/content/audio/test.wav',
        chapters: [
          { id: 'opening', title: 'Opening', start_seconds: 0 },
          { id: 'middle', title: chapterTitles[1], start_seconds: 3 },
          { id: 'ending', title: 'Ending', start_seconds: 10 },
        ] };
      await route.fulfill({ response, body: JSON.stringify(pack) });
    });
    await recorded.route('**/content/audio/test.wav', async route => {
      const range = route.request().headers().range || 'bytes=0-';
      const match = /^bytes=(\d+)-(\d*)$/.exec(range);
      const start = match ? Number(match[1]) : 0;
      const end = match && match[2] ? Math.min(Number(match[2]), audio.length - 1) : audio.length - 1;
      await route.fulfill({
        status: 206,
        contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${audio.length}` },
        body: audio.subarray(start, end + 1),
      });
    });
    await recorded.addInitScript(() => {
      const state = { spoken: [], paused: false, speaking: false };
      class Utterance { constructor(text) { this.text = text; this.rate = 1; } }
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true });
      Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        get paused() { return state.paused; }, get speaking() { return state.speaking; },
        speak(item) { state.spoken.push(item); state.speaking = true; setTimeout(() => item.onstart?.(), 0); },
        pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; }, cancel() { state.paused = false; state.speaking = false; },
      } });
    });
    await recorded.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await recorded.locator('.today-listen-pill').click();
    await recorded.waitForFunction(() => document.querySelector('.today-listen-pill .mode-label')?.textContent === 'Playing · tap to pause');
    await recorded.locator('.mini-player-toggle').focus();
    await recorded.waitForTimeout(1100);
    assert.equal(await recorded.evaluate(() => document.activeElement?.classList.contains('mini-player-toggle')), true,
                 'Progress updates must preserve keyboard focus on the mini-player');
    await recorded.locator('.today-listen-pill').click();
    assert.match(await recorded.locator('.today-listen-pill .mode-label').textContent(), /^Resume/);
    assert.equal(await recorded.locator('.mini-player').count(), 0, 'Paused audio has no bar');
    await recorded.locator('.today-listen-pill').click(); await recorded.locator('.mini-player-open').click(); await recorded.locator('[data-player-toggle]').click();
    assert.equal(await recorded.locator('.player-dialog').isVisible(), true);
    await recorded.locator('[data-player-seek]').evaluate(input => { input.value = '5'; input.dispatchEvent(new Event('change', { bubbles: true })); });
    await recorded.waitForFunction(() => document.querySelector('[data-player-toggle]')?.getAttribute('aria-label')?.startsWith('Resume'));
    assert.match(await recorded.locator('[data-player-toggle]').getAttribute('aria-label'), /^Resume/);
    await recorded.waitForFunction(title => document.querySelector('[data-player-phase]')?.textContent === title, chapterTitles[0]);
    const readingBefore = await recorded.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress);
    await recorded.locator('[data-player-chapters]').click();
    assert.equal(await recorded.locator('[data-player-chapter-list] button').count(), chapterTitles.length);
    assert.equal(await recorded.locator('[data-chapter-index="0"]').getAttribute('aria-current'), 'true');
    assert.equal(await recorded.evaluate(() => document.activeElement?.dataset.chapterIndex), '0',
                 'Opening Chapters should focus the current chapter');
    await recorded.locator('[data-player-back]').click();
    assert.equal(await recorded.evaluate(() => document.activeElement?.hasAttribute('data-player-chapters')), true,
                 'Back to player should return focus to Chapters');
    await recorded.locator('[data-player-chapters]').click();
    if (process.env.BRIEFING_SHOT_DIR) {
      fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
      await recorded.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'web-chapters-light.png') });
      await recorded.emulateMedia({ colorScheme: 'dark' });
      await recorded.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'web-chapters-dark.png') });
      await recorded.emulateMedia({ colorScheme: 'light' });
    }
    await recorded.setViewportSize({ width: 195, height: 844 }); // Approximate a 390 px phone at 200% browser zoom.
    assert.equal(await recorded.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
                 'Chapter list must not create horizontal overflow at 200% zoom');
    if (process.env.BRIEFING_SHOT_DIR) {
      await recorded.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'web-chapters-200-percent-zoom.png') });
    }
    await recorded.setViewportSize({ width: 390, height: 844 });
    await recorded.locator('[data-chapter-index="2"]').click();
    await recorded.waitForFunction(title => !document.querySelector('[data-player-main]')?.hidden && document.querySelector('[data-player-phase]')?.textContent === title, chapterTitles[2]);
    assert.equal(await recorded.evaluate(() => document.activeElement?.hasAttribute('data-player-chapters')), true,
                 'Selecting a chapter should return focus to Chapters');
    assert.match(await recorded.locator('[data-player-toggle]').getAttribute('aria-label'), /^Resume/);
    const readingAfter = await recorded.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress);
    assert.equal(readingAfter['daily-sample-001'].mode, 'listen', 'Selecting a chapter writes the shared listen position');
    assert.equal(readingAfter['daily-sample-001'].storyId, chapterIds[2],
                 'Choosing a chapter stores its corresponding story position');
    await recorded.reload({ waitUntil: 'networkidle' });
    await recorded.locator('.today-listen-pill').waitFor();
    assert.match(await recorded.locator('.today-listen-pill .mode-label').textContent(), /^Resume/);
    assert.equal(await recorded.locator('.mini-player').count(), 0, 'Paused audio has no bar');
    await recorded.locator('.today-listen-pill').click(); await recorded.locator('.mini-player-open').click();
    await recorded.waitForFunction(() => document.querySelector('[data-player-toggle]')?.getAttribute('aria-label') === 'Pause');
    await recorded.locator('[data-player-chapters]').click();
    await recorded.locator('.player-chapter-list [data-chapter-index="0"]').click();
    await recorded.waitForFunction(title => !document.querySelector('[data-player-main]')?.hidden &&
      document.querySelector('[data-player-phase]')?.textContent === title &&
      document.querySelector('[data-player-toggle]')?.getAttribute('aria-label')?.startsWith('Resume'), chapterTitles[0]);
    assert.match(await recorded.locator('[data-player-toggle]').getAttribute('aria-label'), /^Resume/, 'Selecting a chapter saves its paused starting position');
    await recorded.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const pack = await response.json();
      const daily = pack.editions.find(item => item.kind === 'daily');
      daily.audio = { state: 'recorded', revision_id: daily.revision_id, script_hash: daily.script_hash, url: '/content/audio/test.wav',
        chapters: [{ id: 'first', title: 'First', start_seconds: 0 }, { id: 'duplicate', title: 'Invalid', start_seconds: 0 }] };
      await route.fulfill({ response, body: JSON.stringify(pack) });
    });
    await recorded.reload({ waitUntil: 'networkidle' });
    await recorded.locator('.today-listen-pill').click(); await recorded.locator('.mini-player-open').click();
    assert.equal(await recorded.locator('[data-player-chapters]').isVisible(), true, 'Prototype chapters follow the readable story sections, independent of recording metadata');
    await recorded.locator('[data-player-chapters]').click();
    assert.equal(await recorded.locator('[data-player-chapter-list] button').count(), chapterTitles.length);
    assert.deepEqual(playbackErrors, []);
    await recorded.close();
    console.log('Web reader and speech player checks passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
