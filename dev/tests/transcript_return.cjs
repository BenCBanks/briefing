require('../test_guard.cjs').enforceTestEnvironment();

// Focused fictional-data coverage for exact-item transcript routing and return.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8899';

function fixtureWav(seconds = 18) {
  const samples = seconds * 22050;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(22050, 24); wav.writeUInt32LE(44100, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i += 1) wav.writeInt16LE(Math.round(2500 * Math.sin(2 * Math.PI * 330 * i / 22050)), 44 + i * 2);
  return wav;
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.addInitScript(() => { const state = { spoken: [], paused: false, speaking: false, cancelCount: 0 }; class Utterance { constructor(text) { this.text = text; this.rate = 1; } } Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true }); Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: { get paused() { return state.paused; }, get speaking() { return state.speaking; }, speak(item) { state.spoken.push(item); state.speaking = true; setTimeout(() => item.onstart?.(), 0); }, pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; }, cancel() { state.cancelCount += 1; state.paused = false; state.speaking = false; } } }); window.__speech = state; });
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    let materialCorrectionPublished = false;
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const wav = fixtureWav();
    await page.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const weekly = data.editions.find(item => item.kind === 'weekly');
      const oldWeekly = structuredClone(weekly);
      oldWeekly.revision_id = 'sample-week-r3'; oldWeekly.title = 'Superseded fictional weekly version';
      oldWeekly.status = 'superseded';
      oldWeekly.script_hash = 'fictional-old-weekly-hash';
      oldWeekly.sections = oldWeekly.sections.map(section => ({ ...section, id: `old-${section.id}` }));
      oldWeekly.audio = { state: 'recorded', revision_id: oldWeekly.revision_id, script_hash: oldWeekly.script_hash, url: '/content/audio/test.wav' };
      const weeklyCorrection = { id: 'fictional-weekly-correction', severity: 'material', previous_revision_id: oldWeekly.revision_id,
        revision_id: weekly.revision_id, corrected_at: '2026-11-16', section_ids: [weekly.sections[0].id],
        what_changed: 'The fictional weekly recap now separates card authorization, clearing, and final settlement in its payment section.',
        current_text: 'The current fictional recap separates authorization, clearing, and final settlement.',
        why_it_matters: 'That distinction matters because seeing an authorization does not mean the transfer is settled.' };
      weekly.corrections = materialCorrectionPublished ? [weeklyCorrection] : [];
      weekly.audio = { state: 'recorded', revision_id: weekly.revision_id, script_hash: weekly.script_hash, url: '/content/audio/test.wav' };
      const daily = data.editions.find(item => item.kind === 'daily');
      const oldDaily = structuredClone(daily);
      oldDaily.revision_id = 'sample-r3'; oldDaily.title = 'Superseded fictional daily version';
      oldDaily.status = 'superseded';
      oldDaily.script_hash = 'fictional-old-daily-hash';
      oldDaily.sections = oldDaily.sections.map(section => ({ ...section, id: `old-${section.id}` }));
      oldDaily.audio = { state: 'recorded', revision_id: oldDaily.revision_id, script_hash: oldDaily.script_hash, url: '/content/audio/test.wav' };
      const dailyCorrection = { id: 'fictional-daily-correction', severity: 'material', previous_revision_id: oldDaily.revision_id,
        revision_id: daily.revision_id, corrected_at: '2026-11-16', section_ids: [daily.sections[0].id],
        what_changed: 'The daily edition was updated.', current_text: 'Current fictional daily text.',
        why_it_matters: 'The current explanation reflects the reviewed version.' };
      daily.corrections = materialCorrectionPublished ? [dailyCorrection] : [];
      daily.audio = { state: 'recorded', revision_id: daily.revision_id, script_hash: daily.script_hash, url: '/content/audio/test.wav' };
      data.editions.unshift(oldWeekly, oldDaily);
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await page.route('**/content/deep-dives.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const dive = data.deep_dives[0];
      dive.audio = { state: 'recorded', revision_id: dive.revision_id, script_hash: dive.script_hash, url: '/content/audio/test.wav' };
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await page.route('**/content/audio/test.wav', async route => {
      const range = route.request().headers().range || 'bytes=0-';
      const match = /^bytes=(\d+)-(\d*)$/.exec(range);
      const start = match ? Number(match[1]) : 0;
      const end = match && match[2] ? Math.min(Number(match[2]), wav.length - 1) : wav.length - 1;
      await route.fulfill({ status: 206, contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}` }, body: wav.subarray(start, end + 1) });
    });

    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    const todayGreeting = await page.locator('.today-page h1').textContent();
    assert.equal(todayGreeting, 'Your daily briefing');
    assert.deepEqual(await page.locator('.today-top-story-copy strong').allTextContents(), ['A faster checkout', 'Resilience', 'Proof of identity'], 'Top stories point to the current edition');
    assert.deepEqual((await page.locator('.story-row-copy strong').allTextContents()).slice(0,3), ['A faster checkout still needs a way back', 'Resilience is practiced before the outage', 'Proof of identity should reveal only what is needed'], 'The current edition keeps its full story headlines');
    await page.evaluate(() => { location.hash = '#edition/daily/daily-sample-001'; });
    await page.locator('[data-edition="daily-sample-001"]').waitFor();
    assert.equal(await page.locator('.edition h1').textContent(), 'A faster checkout still needs a way back',
      'Stable daily route resolves to its latest non-superseded revision');
    await page.evaluate(() => { location.hash = '#weekly'; });
    await page.locator('.weekly-listen').click();
    await page.waitForFunction(() => document.querySelector('.weekly-listen')?.getAttribute('aria-label')?.startsWith('Pause'));
    await page.waitForTimeout(1200);
    await page.locator('.mini-player-open').click();
    await page.locator('[data-player-toggle]').click();
    await page.waitForFunction(() => /^Resume/.test(document.querySelector('[data-player-toggle]')?.getAttribute('aria-label') || ''));
    await page.locator('.weekly-recap-page').scrollIntoViewIfNeeded();
    const weeklyReaderScroll = await page.evaluate(() => scrollY);
    if (!await page.locator('.player-dialog').count()) await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read along' }).click();
    await page.waitForFunction(() => location.hash.startsWith('#edition/weekly/weekly-sample-001/'));
    assert.match(new URL(page.url()).hash, /^#edition\/weekly\/weekly-sample-001\//, 'Read along opens the exact weekly story from the recap overview');
    await page.locator('body.is-reader .edition-section').waitFor();
    await page.locator('.player-dialog').waitFor({ state: 'detached' });
    assert.equal(await page.locator('.player-dialog').count(), 0, 'Same-reader transcript action only dismisses the player');
    assert.ok(Number.isFinite(weeklyReaderScroll), 'The weekly recap overview preserves a measurable reading position before Read along');
    await page.evaluate(() => { location.hash = '#today'; });
    await page.locator('.today-page').waitFor(); await page.locator('body').click({position:{x:2,y:2}}); await page.keyboard.press('Space');
    await page.locator('.mini-player-open').click();
    const beforeWeekly = await page.evaluate(() => ({
      active: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      playerLabel: document.querySelector('.mini-player-toggle')?.getAttribute('aria-label'),
      time: document.querySelector('.mini-player-open small')?.textContent,
    }));
    await page.getByRole('button', { name: 'Read along' }).click();
    await page.locator('[data-edition="weekly-sample-001"]').waitFor();
    assert.match(new URL(page.url()).hash, /^#edition\/weekly\/weekly-sample-001\//, 'Weekly recording resolves to its exact story route');
    assert.equal(await page.locator('.player-dialog').count(), 0, 'Transcript navigation dismisses the expanded player');
    assert.equal(await page.locator('.edition h1').textContent(), 'Make the commitment visible');
    await page.waitForFunction(() => document.activeElement?.matches('.edition h1, .edition-section h2'));
    if (process.env.BRIEFING_SHOT_DIR) {
      fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
      await page.evaluate(() => {
        document.querySelector('.transcript-return')?.focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: 'instant' });
      });
      await page.waitForTimeout(100);
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'transcript-return-light-390-fictional-test-audio.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'transcript-return-dark-390-fictional-test-audio.png') });
      await page.emulateMedia({ colorScheme: 'light' });
    }
    assert.deepEqual(await page.evaluate(() => ({
      active: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      playerLabel: document.querySelector('.mini-player-toggle')?.getAttribute('aria-label'),
      time: document.querySelector('.mini-player-open small')?.textContent,
    })), beforeWeekly, 'Weekly transcript navigation preserves recording, pause state and position');
    await page.goBack();
    await page.waitForFunction(() => location.hash === '#today');
    await page.waitForFunction(() => document.activeElement?.classList.contains('mini-player-open'));
    assert.equal(await page.locator('.today-page h1').textContent(), todayGreeting,
      'Browser Back returns to the originating Today greeting rather than reopening the transcript');

    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read along' }).click();
    await page.locator('[data-edition="weekly-sample-001"]').waitFor();
    const sourceButton = page.locator('.story-sources [data-source]').first();
    const sourceID = await sourceButton.getAttribute('data-source');
    await sourceButton.click();
    await page.locator('.source-actions').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.transcript-return').waitFor();
    assert.equal(await page.locator('.transcript-return').count(), 1,
      'Transcript origin remains available after a source visit and return');
    assert.match(new URL(page.url()).hash, /^#edition\/weekly\/weekly-sample-001\//,
      'The visible Source Back action returns to the exact transcript reader');
    await page.waitForFunction(id => document.activeElement?.dataset?.source === id, sourceID);
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.source), sourceID,
      'Visible Source Back restores focus to the originating source control');
    await page.goForward();
    await page.locator('.source-actions').waitFor();
    await page.goBack();
    await page.locator('.transcript-return').waitFor();
    await page.waitForFunction(id => document.activeElement?.dataset?.source === id, sourceID);
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.source), sourceID,
      'Browser Back restores focus to the originating source control');
    await page.locator('.transcript-return').click();
    await page.waitForFunction(() => location.hash === '#today');
    await page.goBack();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.match(new URL(page.url()).hash, /^#edition\/weekly\/weekly-sample-001\//,
      'Browser Back reaches the earlier weekly story that Read along opened from the recap overview');

    await page.evaluate(() => { location.hash = '#explanation/dive-payments-basics'; });
    await page.locator('.deep-dive-listen').waitFor();
    const diveAudio = page.locator('.deep-dive-listen');
    await diveAudio.click();
    await page.waitForFunction(() => document.querySelector('.mini-player-toggle')?.getAttribute('aria-label')?.startsWith('Pause'));
    await page.locator('.mini-player-open').click();
    await page.locator('[data-player-toggle]').click();
    await page.waitForFunction(() => /^Resume/.test(document.querySelector('[data-player-toggle]')?.getAttribute('aria-label') || ''));
    await page.locator('[data-player-close]').click();
    await page.evaluate(() => { location.hash = '#library'; });
    await page.locator('.library-filters').waitFor();
    await page.locator('body').click({position:{x:2,y:2}}); await page.keyboard.press('Space');
    await page.locator('.mini-player-open').waitFor();
    const beforeDive = await page.evaluate(() => ({
      active: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      playerLabel: document.querySelector('.mini-player-toggle')?.getAttribute('aria-label'),
      time: document.querySelector('.mini-player-open small')?.textContent,
    }));
    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read along' }).click();
    await page.locator('.deep-dive-sources').waitFor();
    assert.equal(new URL(page.url()).hash, '#explanation/dive-payments-basics', 'Explanation recording resolves by stable item ID');
    assert.deepEqual(await page.evaluate(() => ({
      active: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      playerLabel: document.querySelector('.mini-player-toggle')?.getAttribute('aria-label'),
      time: document.querySelector('.mini-player-open small')?.textContent,
    })), beforeDive, 'Explanation transcript navigation preserves playback and position');
    assert.equal(await page.locator('.reader-back').textContent(), '‹ Earlier',
      'Explanation return preserves the Library origin');
    await page.locator('.transcript-return').click();
    await page.waitForFunction(() => location.hash === '#library');
    await page.waitForFunction(() => document.activeElement?.classList.contains('mini-player-open'));
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('mini-player-open')), true,
      'Library return restores focus to the mini-player');
    assert.deepEqual(errors, []);

    materialCorrectionPublished = true;
    await page.addInitScript(() => {
      const state = JSON.parse(localStorage.getItem('briefing:v1:state'));
      state.activeAudio = { id: 'weekly-sample-001', revisionId: 'sample-week-r3' };
      localStorage.setItem('briefing:v1:state', JSON.stringify(state));
    });
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.locator('.mini-player-toggle:not(:disabled)').count(), 0, 'A material correction prevents a saved edition recording from returning');
    assert.equal(await page.locator('.today-play').count(), 0, 'A material correction hides Today playback');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio), null,
      'A material correction clears the saved edition selection');
    await page.evaluate(() => { location.hash = '#edition/weekly/weekly-sample-001'; });
    await page.locator('[data-edition="weekly-sample-001"] .correction-notice').waitFor();
    assert.match(await page.locator('.correction-notice').innerText(), /Earlier wording:/);
    assert.match(await page.locator('.correction-notice').innerText(), /Current wording: “The current fictional recap/);
    assert.match(await page.locator('.correction-notice').innerText(), /EST/);
    assert.match(await page.locator('.correction-notice').innerText(), /Audio for the whole edition is paused/i);
    assert.equal(await page.locator('.story-listen').count(), 1, 'Material correction preserves a disabled Listen slot on the story page');
    assert.equal(await page.locator('.story-listen').isDisabled(), true, 'Material correction disables listening for the whole edition');
    assert.equal(await page.locator('.edition h1').textContent(), 'Five systems, one useful question',
      'The superseded weekly recording resolves to the current correction');
    assert.equal(await page.locator('.mini-player-toggle:not(:disabled)').count(), 0, 'Corrected text stays readable with no old player');

    const badAudio = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await badAudio.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await badAudio.route('**/content/deep-dives.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const dive = data.deep_dives[0];
      dive.audio = { state: 'recorded', revision_id: dive.revision_id, script_hash: 'mismatched-fictional-hash', url: '/content/audio/test.wav' };
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await badAudio.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await badAudio.evaluate(() => { location.hash = '#explanation/dive-payments-basics'; });
    await badAudio.locator('.deep-dive-listen').waitFor();
    assert.equal(await badAudio.locator('.deep-dive-listen').count(), 1,
      'An explanation remains listenable despite mismatched recording metadata');
    await badAudio.close();

    const staleExplanation = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await staleExplanation.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await staleExplanation.addInitScript(() => localStorage.setItem('briefing:v1:state', JSON.stringify({
      activeAudio: { id: 'dive-payments-basics', revisionId: 'payments-guide-2026-11-16-r1' },
    })));
    await staleExplanation.route('**/content/deep-dives.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const current = data.deep_dives.find(item => item.id === 'dive-payments-basics');
      const old = structuredClone(current);
      old.revision_id = 'payments-guide-2026-11-16-r1'; old.title = 'Older fictional payment explanation';
      old.status = 'superseded';
      old.script_hash = 'older-fictional-explanation-hash';
      old.sections = old.sections.map(section => ({ ...section, id: `old-${section.id}` }));
      old.audio = { state: 'recorded', revision_id: old.revision_id, script_hash: old.script_hash, url: '/content/audio/test.wav' };
      current.audio = { state: 'pending', revision_id: current.revision_id, script_hash: current.script_hash };
      data.deep_dives.unshift(old);
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await staleExplanation.route('**/content/audio/test.wav', async route => {
      const range = route.request().headers().range || 'bytes=0-';
      const match = /^bytes=(\d+)-(\d*)$/.exec(range);
      const start = match ? Number(match[1]) : 0;
      const end = match && match[2] ? Math.min(Number(match[2]), wav.length - 1) : wav.length - 1;
      await route.fulfill({ status: 206, contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}` }, body: wav.subarray(start, end + 1) });
    });
    await staleExplanation.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    assert.equal(await staleExplanation.locator('.mini-player').count(), 0, 'Restored audio stays paused without a bar');
    await staleExplanation.evaluate(()=>{location.hash='#explanation/dive-payments-basics';});
    await staleExplanation.locator('.deep-dive-sources').waitFor();
    assert.equal(await staleExplanation.locator('h1').textContent(), 'Why does a payment that looks instant still need settlement?',
      'A stale explanation recording opens the current item text');
    assert.equal(await staleExplanation.locator('.deep-dive-sources').isVisible(), true);
    assert.equal(new URL(staleExplanation.url()).hash, '#explanation/dive-payments-basics');
    assert.equal(await staleExplanation.locator('.deep-dive-listen').count(), 1,
      'Current explanation remains listenable after stale recording handoff');
    await staleExplanation.close();

    const beforeMissingTranscript = new URL(page.url()).hash;
    await page.evaluate(async () => {
      const { createPlayer } = await import('/src/player.js');
      const root = document.createElement('div'); root.id = 'missing-item-player-test'; document.body.append(root);
      window.missingItemPlayerTest = createPlayer(root, () => {}, () => null, () => {}, () => ({ available: false }));
      window.missingItemPlayerTest.play({ id: 'fictional-item-absent-from-pack', title: 'Unavailable fictional recording',
        kind: 'weekly', revision_id: 'fictional-r1', script_hash: 'fictional-hash', audio: { state: 'recorded', revision_id: 'fictional-r1', script_hash: 'fictional-hash', url: '/content/audio/test.wav' }, sections: [{id:'fictional-unavailable-story',heading:'Fictional unavailable story',body:'This is fictional speech for the unavailable transcript check.'}] });
    });
    const missingPlayer = page.locator('#missing-item-player-test .mini-player-open');
    await missingPlayer.waitFor();
    await missingPlayer.click();
    const missingDialog = page.locator('#missing-item-player-test .player-dialog');
    await missingDialog.waitFor();
    await missingDialog.locator('[data-player-transcript]').click();
    assert.equal(await missingDialog.isVisible(), true, 'Unavailable transcript keeps the expanded player open');
    assert.equal(await missingDialog.locator('[data-player-transcript-error]').textContent(), 'This transcript is unavailable.',
      'Unavailable transcript gives a useful in-player message');
    assert.equal(new URL(page.url()).hash, beforeMissingTranscript, 'Unavailable transcript does not navigate');

    const ambiguous = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await ambiguous.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await ambiguous.addInitScript(() => localStorage.setItem('briefing:v1:state', JSON.stringify({
      activeAudio: { id: 'daily-sample-001', revisionId: 'sample-r3' },
    })));
    await ambiguous.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const current = data.editions.find(item => item.id === 'daily-sample-001');
      delete current.corrections;
      current.audio = { state: 'recorded', revision_id: current.revision_id, script_hash: current.script_hash, url: '/content/audio/test.wav' };
      const otherCurrent = structuredClone(current);
      otherCurrent.revision_id = 'sample-r3'; otherCurrent.title = 'Ambiguous fictional daily revision';
      otherCurrent.script_hash = 'ambiguous-fictional-hash';
      otherCurrent.sections = otherCurrent.sections.map(section => ({ ...section, id: `ambiguous-${section.id}` }));
      otherCurrent.audio = { state: 'recorded', revision_id: otherCurrent.revision_id, script_hash: otherCurrent.script_hash, url: '/content/audio/test.wav' };
      data.editions.unshift(otherCurrent);
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await ambiguous.route('**/content/audio/test.wav', async route => {
      const range = route.request().headers().range || 'bytes=0-';
      const match = /^bytes=(\d+)-(\d*)$/.exec(range);
      const start = match ? Number(match[1]) : 0;
      const end = match && match[2] ? Math.min(Number(match[2]), wav.length - 1) : wav.length - 1;
      await route.fulfill({ status: 206, contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}` }, body: wav.subarray(start, end + 1) });
    });
    await ambiguous.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await ambiguous.getByRole('heading', { name: 'This page is unavailable' }).waitFor();
    assert.equal(await ambiguous.locator('.mini-player').count(), 0, 'Unavailable restored content never exposes idle audio');
    assert.equal(await ambiguous.getByRole('heading', { name: 'This page is unavailable' }).isVisible(), true, 'No stale text is selected');
    await ambiguous.close();

    const withdrawn = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await withdrawn.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await withdrawn.addInitScript(() => localStorage.setItem('briefing:v1:state', JSON.stringify({
      activeAudio: { id: 'daily-sample-001', revisionId: 'sample-r3' },
    })));
    await withdrawn.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const current = data.editions.find(item => item.id === 'daily-sample-001');
      current.status = 'withdrawn';
      current.audio = { state: 'recorded', revision_id: current.revision_id, script_hash: current.script_hash, url: '/content/audio/test.wav' };
      const old = structuredClone(current);
      old.revision_id = 'sample-r3'; old.title = 'Withdrawn fictional daily revision';
      old.status = 'superseded';
      old.script_hash = 'withdrawn-fictional-hash';
      old.sections = old.sections.map(section => ({ ...section, id: `withdrawn-${section.id}` }));
      old.audio = { state: 'recorded', revision_id: old.revision_id, script_hash: old.script_hash, url: '/content/audio/test.wav' };
      data.editions.unshift(old);
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await withdrawn.route('**/content/audio/test.wav', async route => {
      const range = route.request().headers().range || 'bytes=0-';
      const match = /^bytes=(\d+)-(\d*)$/.exec(range);
      const start = match ? Number(match[1]) : 0;
      const end = match && match[2] ? Math.min(Number(match[2]), wav.length - 1) : wav.length - 1;
      await route.fulfill({ status: 206, contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}` }, body: wav.subarray(start, end + 1) });
    });
    await withdrawn.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await withdrawn.getByRole('heading', { name: 'This page is unavailable' }).waitFor();
    assert.equal(await withdrawn.locator('.mini-player').count(), 0, 'Unavailable restored content never exposes idle audio');
    assert.equal(await withdrawn.getByRole('heading', { name: 'This page is unavailable' }).isVisible(), true, 'No stale text is selected');
    await withdrawn.close();
    console.log('Exact-item transcript routing, playback continuity, contextual return and focus passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
