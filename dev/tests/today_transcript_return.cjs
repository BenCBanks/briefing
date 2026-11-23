require('../test_guard.cjs').enforceTestEnvironment();

// Fictional test recording: opening its transcript from the Today front page must enter the reader.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8990';
const wav = Buffer.alloc(44 + 22050 * 2);
wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(22050, 24); wav.writeUInt32LE(44100, 28);
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.addInitScript(() => {
      class Utterance { constructor(text) { this.text = text; this.rate = 1; } }
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true });
      Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        paused: false, speaking: false,
        speak(item) { this.speaking = true; setTimeout(() => item.onstart?.(), 0); },
        pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.paused = false; this.speaking = false; },
      } });
    });
    await page.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const daily = data.editions.find(item => item.kind === 'daily');
      daily.audio = { state: 'recorded', revision_id: daily.revision_id,
        script_hash: daily.script_hash, url: '/content/audio/test.wav' };
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await page.route('**/content/audio/test.wav', route => route.fulfill({ contentType: 'audio/wav', body: wav }));
    await page.goto(`${baseURL}/#edition/daily/daily-sample-001`, { waitUntil: 'networkidle' });
    await page.locator('.story-listen').click();
    await page.locator('.mini-player-open').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    assert.equal(new URL(page.url()).hash, '#today');
    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read along' }).click();
    await page.waitForFunction(() => location.hash.startsWith('#edition/daily/daily-sample-001/'));
    assert.equal(await page.locator('.edition-section').count(), 1, 'Transcript opens the daily story page');
    assert.equal(await page.locator('.transcript-return').count(), 1, 'Reader retains its Today origin');
    await page.locator('.transcript-return').click();
    await page.locator('.today-page').waitFor();
    assert.equal(new URL(page.url()).hash, '#today');
    console.log('Today front page transcript opens the daily reader and returns to Today.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
