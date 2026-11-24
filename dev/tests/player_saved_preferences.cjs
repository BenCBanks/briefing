require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8945';
const shotDir = process.env.BRIEFING_SHOT_DIR;

async function capture(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ path: path.join(shotDir, `${name}.png`), animations: 'disabled' });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
    await page.addInitScript(() => {
      const state = { spoken: [] };
      class Utterance { constructor(text) { this.text = text; this.rate = 1; } }
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { configurable: true, value: Utterance });
      Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        getVoices: () => [{ name: 'Fictional Natural Voice', lang: 'en-US', voiceURI: 'fixture-natural', default: true }],
        speak(item) { state.spoken.push(item); queueMicrotask(() => item.onstart?.()); },
        pause() {}, resume() {}, cancel() {},
      } });
      localStorage.setItem('briefing:playback-speed', '1.25');
      localStorage.setItem('briefing:narrator-sample', 'D');
      localStorage.setItem('briefing:voice-uri', 'fixture-natural');
      window.__savedPreferenceFixture = state;
    });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });

    const first = await page.evaluate(async () => {
      const { createPlayer } = await import('/src/player.js');
      const root = document.createElement('div'); document.body.append(root);
      const player = createPlayer(root, () => {}, () => null);
      return { speed: player.speed, narrator: player.narratorSample, voice: player.voice?.voiceURI };
    });
    assert.deepEqual(first, { speed: 1.25, narrator: 'D', voice: 'fixture-natural' },
      'a visit restores the saved listening speed and narrator');

    const second = await page.evaluate(async () => {
      const { createPlayer } = await import('/src/player.js');
      const root = document.createElement('div'); document.body.append(root);
      const player = createPlayer(root, () => {}, () => null);
      const speed = document.createElement('select');
      for (const value of [0.75, 1, 1.25, 1.5, 2]) {
        const option = document.createElement('option'); option.value = String(value); speed.append(option);
      }
      player.configureVoiceSettings(null, speed, null);
      speed.value = '2'; speed.dispatchEvent(new Event('change'));
      player.setNarratorSample('B');
      player.close();

      const freshRoot = document.createElement('div'); freshRoot.id = 'fresh-episode-player'; document.body.append(freshRoot);
      const fresh = createPlayer(freshRoot, () => {}, () => null);
      fresh.listen({ id: 'fictional-new-episode', revision_id: 'fixture-r1', kind: 'episode',
        title: 'A new fictional episode', sections: [{ id: 'fictional-chapter', heading: 'A fresh fictional chapter',
          body: 'This new episode uses only fictional fixture text.' }] });
      return { speed: fresh.speed, narrator: fresh.narratorSample, voice: fresh.voice?.voiceURI,
        savedSpeed: localStorage.getItem('briefing:playback-speed'),
        savedNarrator: localStorage.getItem('briefing:narrator-sample') };
    });
    await page.waitForFunction(() => window.__savedPreferenceFixture.spoken.length > 0);
    await page.locator('#fresh-episode-player .mini-player-open').click();
    second.label = await page.locator('.player-voice-label').innerText();
    assert.deepEqual(second, { speed: 2, narrator: 'B', voice: 'fixture-natural', label: 'Voice: Charon',
      savedSpeed: '2', savedNarrator: 'B' }, 'a new episode uses the listener’s saved speed and narrator');
    await capture(page, 'new-episode-player-light-390');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'new-episode-player-dark-390');
    console.log('Player saved preferences: speed and narrator restore and apply to a new episode.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
