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
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseURL, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.edition-play').count(), 0, 'Pending audio must not promise Play');
    assert.equal(await page.locator('.mini-player').count(), 0, 'Pending audio must not create a player');
    const layout = await page.evaluate(() => ({
      articleTop: document.querySelector('.edition-story').getBoundingClientRect().top,
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    assert.ok(layout.articleTop < 500, 'Article should begin in the first phone screen');
    assert.equal(layout.overflow, false);
    await page.getByRole('button', { name: /View coverage/ }).click();
    assert.equal(await page.locator('dialog.coverage-dialog').isVisible(), true);
    await page.locator('dialog.coverage-dialog [data-close]').click();
    await page.locator('[data-source]').first().click();
    await page.locator('.source-details').waitFor();
    assert.equal(await page.getByRole('link', { name: /Open the original source/ }).count(), 1,
                 'External references keep their original-source link');
    await page.getByRole('button', { name: /Return to briefing/ }).click();
    await page.locator('[data-finish]').scrollIntoViewIfNeeded();
    const clear = await page.evaluate(() => document.querySelector('[data-finish]').getBoundingClientRect().bottom < document.querySelector('.main-nav').getBoundingClientRect().top);
    assert.equal(clear, true, 'Bottom navigation must not cover the finish action');
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

    const recorded = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const audio = fixtureWav(20);
    const longChapter = 'How a faster checkout changes the choices people can make';
    const playbackErrors = [];
    recorded.on('pageerror', error => playbackErrors.push(error.message));
    await recorded.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const pack = await response.json();
      const daily = pack.editions.find(item => item.kind === 'daily');
      daily.audio = { state: 'recorded', revision_id: daily.revision_id, script_hash: daily.script_hash, url: '/content/audio/test.wav',
        chapters: [
          { id: 'opening', title: 'Opening', start_seconds: 0 },
          { id: 'middle', title: longChapter, start_seconds: 3 },
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
    await recorded.goto(baseURL, { waitUntil: 'networkidle' });
    await recorded.getByRole('button', { name: 'Play briefing' }).click();
    await recorded.waitForFunction(() => document.querySelector('.edition-play')?.textContent === 'Pause');
    await recorded.locator('.mini-player-toggle').focus();
    await recorded.waitForTimeout(1100);
    assert.equal(await recorded.evaluate(() => document.activeElement?.classList.contains('mini-player-toggle')), true,
                 'Progress updates must preserve keyboard focus on the mini-player');
    await recorded.locator('.edition-play').click();
    assert.match(await recorded.locator('.edition-play').textContent(), /^Resume/);
    await recorded.locator('.mini-player-open').click();
    assert.equal(await recorded.locator('.player-dialog').isVisible(), true);
    await recorded.locator('[data-player-seek]').evaluate(input => { input.value = '5'; input.dispatchEvent(new Event('change', { bubbles: true })); });
    await recorded.waitForFunction(() => document.querySelector('[data-player-toggle]')?.textContent?.includes('0:05'));
    assert.match(await recorded.locator('[data-player-toggle]').textContent(), /^Resume/);
    await recorded.waitForFunction(title => document.querySelector('[data-player-phase]')?.textContent === title, longChapter);
    const readingBefore = await recorded.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress);
    await recorded.locator('[data-player-chapters]').click();
    assert.equal(await recorded.locator('[data-player-chapter-list] button').count(), 3);
    assert.match(await recorded.locator('[data-chapter-index="1"]').getAttribute('aria-label'), /current chapter/);
    assert.equal(await recorded.evaluate(() => document.activeElement?.dataset.chapterIndex), '1',
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
    await recorded.waitForFunction(() => !document.querySelector('[data-player-main]')?.hidden && document.querySelector('[data-player-phase]')?.textContent === 'Ending');
    assert.equal(await recorded.evaluate(() => document.activeElement?.hasAttribute('data-player-chapters')), true,
                 'Selecting a chapter should return focus to Chapters');
    assert.match(await recorded.locator('[data-player-toggle]').textContent(), /^Resume/);
    const readingAfter = await recorded.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress);
    assert.deepEqual(readingAfter, readingBefore, 'Choosing a chapter must not change reading position');
    await recorded.reload({ waitUntil: 'networkidle' });
    await recorded.waitForFunction(() => document.querySelector('.mini-player-open small')?.textContent?.includes('0:10'));
    assert.match(await recorded.locator('.edition-play').textContent(), /^Resume/);
    await recorded.locator('.mini-player-open').click();
    await recorded.locator('[data-player-toggle]').click();
    await recorded.waitForFunction(() => document.querySelector('[data-player-toggle]')?.textContent === 'Pause');
    await recorded.locator('[data-player-chapters]').click();
    await recorded.locator('[data-chapter-index="0"]').click();
    await recorded.waitForFunction(() => !document.querySelector('[data-player-main]')?.hidden && document.querySelector('[data-player-phase]')?.textContent === 'Opening');
    assert.equal(await recorded.locator('[data-player-toggle]').textContent(), 'Pause', 'Playing chapter selection must keep playing');
    await recorded.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const pack = await response.json();
      const daily = pack.editions.find(item => item.kind === 'daily');
      daily.audio = { state: 'recorded', revision_id: daily.revision_id, script_hash: daily.script_hash, url: '/content/audio/test.wav',
        chapters: [{ id: 'first', title: 'First', start_seconds: 0 }, { id: 'duplicate', title: 'Invalid', start_seconds: 0 }] };
      await route.fulfill({ response, body: JSON.stringify(pack) });
    });
    await recorded.reload({ waitUntil: 'networkidle' });
    await recorded.locator('.mini-player-open').click();
    assert.equal(await recorded.locator('[data-player-chapters]').isVisible(), false, 'Invalid chapter boundaries must hide Chapters');
    assert.deepEqual(playbackErrors, []);
    await recorded.close();
    console.log('Web reader and recorded playback checks passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
