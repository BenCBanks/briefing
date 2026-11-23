require('../test_guard.cjs').enforceTestEnvironment();

// Test-only two-revision correction preview. Fictional fixture packs and WAV stay outside app content.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8898';
const fixtureRoot = path.resolve(__dirname, '../fixtures/correction');
const files = ['topics.json', 'deep-dives.json', 'sources.json', 'editions.json'];
const captureDir = process.env.BRIEFING_CAPTURE_DIR;

async function capture(page, name) {
  if (!captureDir) return;
  fs.mkdirSync(captureDir, { recursive: true });
  await page.screenshot({ path: path.join(captureDir, name), fullPage: false });
}

function fixtureWav(seconds = 12) {
  const samples = seconds * 22050;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(22050, 24);
  wav.writeUInt32LE(44100, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(2500 * Math.sin(2 * Math.PI * 440 * i / 22050)), 44 + i * 2);
  return wav;
}

async function installPack(page, getRevision, { recordedR1 = false, staleR2 = false } = {}) {
  // Keep the saved server profile and Vale's current generated cache out of this test;
  // exercise only the fictional r1/r2 correction pack below.
  await page.route('**/api/profile', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
  await page.route('**/api/edition**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generated: false }) }));
  await page.route('**/content/*.json', async route => {
    const name = path.basename(new URL(route.request().url()).pathname);
    if (!files.includes(name)) return route.continue();
    const revision = getRevision();
    const body = JSON.parse(fs.readFileSync(path.join(fixtureRoot, revision, name), 'utf8'));
    if (name === 'editions.json' && revision === 'r1' && recordedR1) {
      const item = body.editions.find(edition => edition.kind === 'daily');
      item.audio = { state: 'recorded', revision_id: item.revision_id, script_hash: item.script_hash,
        url: '/content/audio/correction-test.wav', duration_seconds: 12, chapters: [
          { id: 'fictional-pilot-overview', title: 'A checkout pilot counted 120 participants', start_seconds: 0 },
          { id: 'fictional-pilot-follow-up', title: 'Follow-up and alternate ways to resolve duplicate authorizations', start_seconds: 6 },
        ] };
    }
    if (name === 'editions.json' && revision === 'r2' && staleR2) {
      const item = body.editions.find(edition => edition.kind === 'daily');
      item.audio = { state: 'recorded', revision_id: 'fictional-r1', script_hash: 'old-script-hash',
        url: '/content/audio/correction-test.wav', duration_seconds: 12, chapters: [] };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.route('**/content/audio/correction-test.wav', async route => {
    const audio = fixtureWav();
    const header = route.request().headers().range || 'bytes=0-';
    const match = /^bytes=(\d+)-(\d*)$/.exec(header);
    const start = match ? Number(match[1]) : 0;
    const end = match && match[2] ? Math.min(Number(match[2]), audio.length - 1) : audio.length - 1;
    await route.fulfill({ status: 206, contentType: 'audio/wav', headers: {
      'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${audio.length}`,
    }, body: audio.subarray(start, end + 1) });
  });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    let revision = 'r1';
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await installPack(page, () => revision);
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.story-row-link').first().click();
    await page.getByRole('button', { name: 'Save' }).click();

    revision = 'r2';
    await page.reload({ waitUntil: 'networkidle' });
    assert.match(await page.locator('.correction-notice').innerText(), /120 to 12/);
    assert.match(await page.locator('.correction-notice').innerText(), /Audio for the whole edition is paused/);
    assert.equal(await page.locator('.story-listen').count(), 1, 'Corrected text keeps a Listen slot');
    assert.equal(await page.locator('.story-listen').isDisabled(), true, 'Corrected story Listen is disabled');
    assert.match(await page.locator('.story-listen').innerText(), /Audio paused/);
    await capture(page, 'fictional-correction-today-light.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'fictional-correction-today-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    await page.locator('[data-read-section="correction-checkout-pilot"]').click();
    await page.locator('#correction-checkout-pilot').waitFor();
    assert.match(await page.locator('.reader-sticky .story-progress').getAttribute('aria-label'), /Story 1 of 1/);
    assert.match(await page.locator('#correction-checkout-pilot').innerText(), /12 participants/);
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress['fictional-correction-edition']?.revisionId === 'fictional-r2');
    const readingProgress = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress['fictional-correction-edition']);
    await page.locator('.story-sources').waitFor();
    await page.locator('.story-sources [data-source="fictional-pilot-record"]').click();
    await page.getByRole('heading', { name: 'Fictional pilot record — demonstration source' }).waitFor();
    await page.waitForFunction(() => document.activeElement?.tagName === 'H1');
    await capture(page, 'fictional-correction-source-light.png');
    assert.equal(await page.getByRole('link', { name: /Open original/ }).count(), 0, 'Internal fictional source has no external link');
    assert.equal(await page.locator('.source-publisher').innerText(), 'Fictional demonstration source');
    assert.equal(await page.locator('.source-published').innerText(), 'Document date · 16 Nov');
    assert.match(await page.locator('.source-title').innerText(), /Fictional pilot record/);
    await page.setViewportSize({ width: 195, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
      'Source details reflow without horizontal overflow at narrow zoom width');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.reader-back').click();
    await page.locator('#correction-checkout-pilot').waitFor();
    const returnedSection = await page.locator('#correction-checkout-pilot').evaluate(section => section.getBoundingClientRect());
    assert.ok(returnedSection.top < 844 && returnedSection.bottom > 0, 'Source return restores the corrected story in view');
    await page.waitForFunction(() => document.activeElement?.dataset?.source === 'fictional-pilot-record');
    const returnedReading = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress['fictional-correction-edition']);
    assert.equal(returnedReading.storyId, readingProgress.storyId, 'Source return keeps the same story anchor');
    assert.equal(returnedReading.revisionId, readingProgress.revisionId, 'Source return keeps the same text revision');
    assert.ok(Number.isInteger(returnedReading.paragraphIndex) && returnedReading.paragraphIndex >= 0, 'Source return keeps an exact paragraph position');
    assert.ok(Number.isFinite(Date.parse(returnedReading.updatedAt)), 'Reading position keeps a valid update timestamp');
    await page.goto(`${baseURL}/#library`, { waitUntil: 'networkidle' });
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).savedIds), ['fictional-correction-edition'], 'The stable item remains exactly one saved Library entry');
    assert.equal((await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).savedRevisions['fictional-correction-edition'])), 'fictional-r1', 'The saved revision is retained as metadata');
    await capture(page, 'fictional-correction-library-light.png');
    assert.match(await page.locator('.library-briefing-list').innerText(), /Corrected/, 'Earlier date row links to the corrected-edition state');
    assert.match(await page.locator('.library-briefing-list li').first().innerText(), /\d+ stories · \d+ min read/, 'Earlier date row reports actual stories and estimated reading time');
    await page.getByRole('button', { name: 'Saved' }).click();
    assert.match(await page.locator('.library-list').innerText(), /Corrected/, 'The saved-items section retains the corrected status');
    const savedRow = page.locator('.library-row').filter({ hasText: 'A fictional checkout pilot' });
    assert.deepEqual(await savedRow.locator('.library-status-tag').allTextContents(), ['Corrected', 'Updated'], 'Corrected and Updated stay as distinct orange row labels');
    assert.equal(await page.locator('.library-filters button').count(), 3, 'Earlier has Editions, Episodes and Saved filters');
    await capture(page, 'fictional-correction-library-saved-light.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'fictional-correction-library-saved-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });
    assert.doesNotMatch(await page.locator('.library-list').innerText(), /fictional-r1/, 'Revision IDs stay out of the reader');
    await page.getByRole('link', { name: /fictional checkout pilot/i }).click();
    assert.match(await page.locator('#correction-checkout-pilot').innerText(), /12 participants/);

    // Recorded r1 audio exists only in this browser test; reload rejects its stale selection when r2 loads.
    revision = 'r1';
    const active = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await active.addInitScript(() => { const state = { spoken: [], paused: false, speaking: false, cancelCount: 0 }; class Utterance { constructor(text) { this.text = text; this.rate = 1; } } Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true }); Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: { get paused() { return state.paused; }, get speaking() { return state.speaking; }, speak(item) { state.spoken.push(item); state.speaking = true; setTimeout(() => item.onstart?.(), 0); }, pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; }, cancel() { state.cancelCount += 1; state.paused = false; state.speaking = false; } } }); window.__speech = state; });
    await installPack(active, () => revision, { recordedR1: true });
    await active.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await active.locator('.today-listen-pill').click();
    await active.locator('.today-story-row .story-row-link').first().click();
    await active.locator('.story-listen').click();
    await active.waitForFunction(() => window.__speech.spoken.length > 0 && document.querySelector('.mini-player-toggle')?.getAttribute('aria-label')?.startsWith('Pause'));
    const miniOpen = active.locator('.mini-player-open');
    assert.match(await miniOpen.getAttribute('aria-label'), /A checkout pilot counted 120 participants, Story 1 of 1, \d+:\d+, about \d+ minutes left, opens player/,
      'The mini-player opener announces the story, chapter count, elapsed time and estimated time remaining');
    assert.match(await active.locator('.mini-player-toggle').getAttribute('aria-label'), /^Pause\b/, 'The mini-player action reflects actual playback');
    await miniOpen.click();
    await active.locator('.player-dialog[open]').waitFor();
    assert.equal(await active.evaluate(() => document.activeElement?.dataset?.playerClose !== undefined), true, 'Opening the modal moves focus into the player');
    await active.keyboard.press('Shift+Tab');
    assert.equal(await active.evaluate(() => document.activeElement?.dataset?.playerTranscript !== undefined), true, 'Tab focus wraps within the modal dialog');
    await active.keyboard.press('Tab');
    assert.equal(await active.evaluate(() => document.activeElement?.dataset?.playerClose !== undefined), true, 'Tab focus cycles back to the first modal control');
    assert.equal(await active.locator('[data-player-chapters]').isVisible(), false, 'One story has no redundant chapter list');
    await active.keyboard.press('Escape');
    await active.locator('.player-dialog').waitFor({ state: 'detached' });
    assert.equal(await active.evaluate(() => document.activeElement?.classList.contains('mini-player-open')), true, 'Escape returns focus to the mini-player opener');
    await active.locator('.mini-player-open').click();
    await active.getByRole('button', { name: 'Minimize' }).click();
    assert.equal(await active.evaluate(() => document.activeElement?.classList.contains('mini-player-open')), true, 'Minimize returns focus to the mini-player opener');
    await active.evaluate(() => window.dispatchEvent(new CustomEvent('briefing:material-correction', { detail: { id: 'fictional-correction-edition' } })));
    await active.waitForFunction(() => window.__speech.cancelCount > 0 && !document.querySelector('.mini-player-toggle:not(:disabled)'));
    assert.equal(await active.locator('.story-listen').count(), 1, 'A correction keeps the story Listen slot visible');
    assert.equal(await active.locator('.story-listen').isDisabled(), true, 'A correction disables Listen from here immediately');
    assert.match(await active.locator('.story-listen').innerText(), /Audio paused/);
    const stoppedState = await active.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')));
    assert.equal(stoppedState.activeAudio, null, 'A material correction clears active audio immediately');
    assert.equal(stoppedState.progress['fictional-correction-edition'], undefined, 'A material correction removes the saved listening position');
    revision = 'r2';
    await active.reload({ waitUntil: 'networkidle' });
    assert.equal(await active.locator('.story-listen').count(), 1, 'Corrected text keeps the story Listen slot after reload');
    assert.equal(await active.locator('.story-listen').isDisabled(), true, 'No old or updated audio is offered after correction');
    assert.equal(await active.locator('.mini-player-toggle:not(:disabled)').count(), 0, 'Old selected audio is not restored');
    assert.match(await active.locator('.correction-notice').innerText(), /Audio for the whole edition is paused/i);
    await active.locator('.reader-back').click();
    await active.locator('.today-page').waitFor();
    assert.equal(await active.locator('.today-listen-pill').isDisabled(), true, 'Today keeps Listen visible but disabled after a material correction');
    assert.match(await active.locator('.today-listen-pill').innerText(), /Audio paused/i);
    const saved = await active.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')));
    assert.equal(saved.activeAudio, null, 'The superseded selection is cleared');
    await active.close();

    // A recorded asset whose revision/hash do not match is ignored while corrected text stays readable.
    revision = 'r2';
    const stale = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await installPack(stale, () => revision, { staleR2: true });
    await stale.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    assert.equal(await stale.locator('.today-listen-pill').isDisabled(), true, 'Revision/hash mismatch never exposes active Listen');
    assert.equal(await stale.locator('.mini-player-toggle:not(:disabled)').count(), 0, 'Revision/hash mismatch cannot restore a player');
    await stale.locator('.story-row-link').first().click();
    assert.match(await stale.locator('#correction-checkout-pilot').innerText(), /12 participants/);
    await stale.close();
    await page.close();
    console.log('Correction preview: save across revisions, corrected-section/source return, stale audio rejection after reload and test-only WAV isolation passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
