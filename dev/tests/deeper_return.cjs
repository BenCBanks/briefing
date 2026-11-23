require('../test_guard.cjs').enforceTestEnvironment();

// Fictional-data journey: one long story, matching audio chapter, explanation/source return, then Next story.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8931';

function fixtureWav(seconds = 20) {
  const samples = seconds * 22050;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(22050, 24);
  wav.writeUInt32LE(44100, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(3000 * Math.sin(2 * Math.PI * 440 * i / 22050)), 44 + i * 2);
  return wav;
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const errors = [];
    await page.addInitScript(() => { const state = { spoken: [], paused: false, speaking: false }; class Utterance { constructor(text) { this.text = text; this.rate = 1; } } Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true }); Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: { get paused() { return state.paused; }, get speaking() { return state.speaking; }, speak(item) { state.spoken.push(item); state.speaking = true; setTimeout(() => item.onstart?.(), 0); }, pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; }, cancel() { state.paused = false; state.speaking = false; } } }); window.__speech = state; });
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    page.on('pageerror', error => errors.push(error.message));
    const wav = fixtureWav();
    // Keep the current generated cache out; this journey targets the sample edition below.
    await page.route('**/api/edition**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generated: false }) }));
    await page.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const pack = await response.json();
      const daily = pack.editions.find(item => item.kind === 'daily');
      const first = daily.sections[0];
      const longFictionalParagraph = 'This invented fictional passage gives the reader enough text to test a genuinely long story page, saved reading position, explanation navigation, and return behavior without using any real article or personal information. '.repeat(4);
      first.body = Array.from({ length: 16 }, (_, index) =>
        `Fictional paragraph ${index + 1}. ${longFictionalParagraph}`
      ).join('\n\n');
      daily.audio = { state: 'recorded', revision_id: daily.revision_id, script_hash: daily.script_hash, url: '/content/audio/fixture.wav', duration_seconds: 20,
        chapters: [{ id: 'fictional-chapter-one', section_id: first.id, title: 'A faster checkout still needs a way back', start_seconds: 6 }] };
      await route.fulfill({ response, body: JSON.stringify(pack) });
    });
    await page.route('**/content/audio/fixture.wav', async route => {
      const range = route.request().headers().range || 'bytes=0-';
      const match = /^bytes=(\d+)-(\d*)$/.exec(range);
      const start = match ? Number(match[1]) : 0;
      const end = match && match[2] ? Math.min(Number(match[2]), wav.length - 1) : wav.length - 1;
      await route.fulfill({ status: 206, contentType: 'audio/wav', headers: {
        'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}`,
      }, body: wav.subarray(start, end + 1) });
    });

    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-contents [data-read-section]').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.edition-section').count(), 1, 'Each reader route displays one story');
    assert.match(await page.locator('.reader-sticky .story-progress').getAttribute('aria-label'), /Story 1 of 4/);
    await page.getByRole('button', { name: 'Listen from here' }).click();
    await page.waitForFunction(() => document.querySelector('.mini-player-toggle')?.getAttribute('aria-label')?.startsWith('Pause'));
    await page.waitForFunction(() => window.__speech.spoken.length > 0);
    assert.match(await page.locator('.mini-player-open').getAttribute('aria-label'), /A faster checkout still needs a way back/, 'Listen from here selects the matching story chapter');
    await page.locator('.edition-section').scrollIntoViewIfNeeded();
    const readDeeper = page.locator('.section-explanation');
    await readDeeper.scrollIntoViewIfNeeded();
    const targetY = await page.evaluate(() => scrollY);
    assert.ok(targetY > 1000, 'The story page supports a long reading position');
    const topicID = await readDeeper.getAttribute('data-topic');
    const before = await page.evaluate(() => ({
      activeAudio: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      reading: JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress,
    }));
    assert.ok(before.activeAudio?.id, 'Audio is selected before the explanation opens');
    await readDeeper.click();
    await page.locator('.deep-dive-sources').waitFor();
    assert.equal(await page.locator('.reader-back').count(), 1, 'Explanation has exactly one Back control');
    assert.match(await page.locator('.reader-back').innerText(), /Story 1/, 'Back names the story that opened the explanation');
    assert.match(await page.locator('.deep-dive-topline').innerText(), /Sample/i);
    assert.match(await page.locator('.deep-dive-listen').innerText(), /Listen · about \d+ min/, 'Pending explanation has a Listen control');
    const source = page.locator('.source-internal-link').first();
    assert.ok(await source.count(), 'Explanation shows its own source records');
    await source.click();
    await page.locator('.source-title').waitFor();
    assert.equal(await page.locator('.reader-back').count(), 1, 'Source page has exactly one Back control');
    assert.match(await page.locator('.reader-back').innerText(), /Explanation/, 'Source Back names its parent');
    await page.locator('.reader-back').click();
    await page.locator('.deep-dive-page').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('body.is-reader .edition-section').waitFor();
    await page.waitForFunction(y => Math.abs(scrollY - y) < 24, targetY); // route restores scroll on the next animation frame
    const after = await page.evaluate(() => ({
      y: scrollY,
      activeAudio: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      reading: JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress,
    }));
    assert.ok(Math.abs(after.y - targetY) < 24, `Story place restored within one compact sticky-header offset (${targetY} → ${after.y})`);
    assert.deepEqual(after.activeAudio, before.activeAudio, 'Opening an explanation does not switch audio');
    assert.deepEqual(after.progress, before.progress, 'Opening an explanation does not change audio position');
    assert.equal(after.reading['daily-sample-001'].storyId, before.reading['daily-sample-001'].storyId, 'Explanation return preserves the story anchor');
    assert.equal(after.reading['daily-sample-001'].revisionId, before.reading['daily-sample-001'].revisionId, 'Explanation return preserves the text revision');
    assert.ok(Number.isInteger(after.reading['daily-sample-001'].paragraphIndex) && after.reading['daily-sample-001'].paragraphIndex >= 0 && after.reading['daily-sample-001'].paragraphIndex < 16,
      'Restored long-story scroll retains an exact paragraph index within the 16-paragraph story');
    assert.equal(after.reading['daily-sample-001'].mode, 'listen', 'The shared position remains in listening mode while narration continues');
    assert.equal(after.reading['dive-payments-basics']?.revisionId, 'payments-guide-2026-11-16-r2', 'The explanation saves its own revision-specific reading place');

    await page.locator('.next-story').click();
    await page.waitForFunction(() => location.hash.includes('/daily-banking-001'));
    assert.equal(await page.locator('.edition-section').count(), 1);
    assert.match(await page.locator('.reader-sticky .story-progress').getAttribute('aria-label'), /Story 2 of 4/);
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    const finishedRow = page.locator('[data-read-section]').first();
    assert.equal(await finishedRow.evaluate(element => element.classList.contains('is-read')), true, 'Today dims a story after it is passed as read without adding status text');
    assert.equal(await finishedRow.locator('.story-states,.story-state').count(), 0, 'Finished stories have no Read label');
    await page.setViewportSize({ width: 195, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Story route remains usable at narrow zoom width');
    assert.deepEqual(errors, []);
    console.log('Deeper return: story page, matching chapter, explanation/source return, reading place, audio continuity and Next story passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
