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
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const wav = fixtureWav();
    await page.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const weekly = data.editions.find(item => item.kind === 'weekly');
      const oldWeekly = structuredClone(weekly);
      oldWeekly.revision_id = 'sample-week-r3'; oldWeekly.title = 'Superseded fictional weekly version';
      oldWeekly.script_hash = 'fictional-old-weekly-hash';
      oldWeekly.sections = oldWeekly.sections.map(section => ({ ...section, id: `old-${section.id}` }));
      oldWeekly.audio = { state: 'recorded', revision_id: oldWeekly.revision_id, script_hash: oldWeekly.script_hash, url: '/content/audio/test.wav' };
      weekly.corrections = [{ id: 'fictional-weekly-correction', severity: 'material', previous_revision_id: oldWeekly.revision_id,
        revision_id: weekly.revision_id, corrected_at: '2026-11-16', section_ids: [weekly.sections[0].id],
        what_changed: 'The fictional weekly recap now separates card authorization, clearing, and final settlement in its payment section.',
        why_it_matters: 'That distinction matters because seeing an authorization does not mean the transfer is settled.' }];
      weekly.audio = { state: 'recorded', revision_id: weekly.revision_id, script_hash: weekly.script_hash, url: '/content/audio/test.wav' };
      const daily = data.editions.find(item => item.kind === 'daily');
      const oldDaily = structuredClone(daily);
      oldDaily.revision_id = 'sample-r3'; oldDaily.title = 'Superseded fictional daily version';
      oldDaily.script_hash = 'fictional-old-daily-hash';
      oldDaily.sections = oldDaily.sections.map(section => ({ ...section, id: `old-${section.id}` }));
      oldDaily.audio = { state: 'recorded', revision_id: oldDaily.revision_id, script_hash: oldDaily.script_hash, url: '/content/audio/test.wav' };
      daily.corrections = [{ id: 'fictional-daily-correction', severity: 'material', previous_revision_id: oldDaily.revision_id,
        revision_id: daily.revision_id, corrected_at: '2026-11-16', section_ids: [daily.sections[0].id],
        what_changed: 'The daily edition was updated.', why_it_matters: 'The current explanation reflects the reviewed version.' }];
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

    await page.goto(baseURL, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.today-page h1').textContent(), 'The choices behind the tap',
      'Today resolves its current daily edition despite an older same-ID revision');
    await page.evaluate(() => { location.hash = '#edition/daily/daily-sample-001'; });
    await page.locator('[data-edition="daily-sample-001"]').waitFor();
    assert.equal(await page.locator('.edition h1').textContent(), 'The choices behind the tap',
      'Stable daily route resolves to its latest non-superseded revision');
    await page.evaluate(() => { location.hash = '#weekly'; });
    await page.getByRole('button', { name: 'Play recap' }).click();
    await page.waitForFunction(() => document.querySelector('.edition-play')?.textContent === 'Pause');
    await page.waitForTimeout(1200);
    await page.locator('.edition-play').click();
    await page.waitForFunction(() => /^Resume/.test(document.querySelector('.edition-play')?.textContent || ''));
    await page.locator('.edition-section').last().scrollIntoViewIfNeeded();
    const weeklyReaderScroll = await page.evaluate(() => scrollY);
    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read transcript' }).click();
    assert.equal(new URL(page.url()).hash, '#weekly', 'The same weekly reader does not receive a duplicate route');
    await page.locator('.player-dialog').waitFor({ state: 'detached' });
    assert.equal(await page.locator('.player-dialog').count(), 0, 'Same-reader transcript action only dismisses the player');
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - weeklyReaderScroll) < 2,
      'Same-reader transcript action preserves the existing scroll position');
    await page.evaluate(() => { location.hash = '#today'; });
    await page.locator('.mini-player-open').click();
    const beforeWeekly = await page.evaluate(() => ({
      active: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      playerLabel: document.querySelector('.mini-player-toggle')?.getAttribute('aria-label'),
      time: document.querySelector('.mini-player-open small')?.textContent,
    }));
    await page.getByRole('button', { name: 'Read transcript' }).click();
    await page.locator('[data-edition="weekly-sample-001"]').waitFor();
    assert.equal(new URL(page.url()).hash, '#edition/weekly/weekly-sample-001', 'Weekly recording resolves to its exact reader route');
    assert.equal(await page.locator('.player-dialog').count(), 0, 'Transcript navigation dismisses the expanded player');
    assert.equal(await page.locator('.edition h1').textContent(), 'Five systems, one useful question');
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
    assert.equal(await page.locator('.today-page h1').textContent(), 'The choices behind the tap',
      'Browser Back returns to the originating screen rather than reopening the transcript');

    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read transcript' }).click();
    await page.locator('[data-edition="weekly-sample-001"]').waitFor();
    const sourceButton = page.locator('.edition-section [data-source]').first();
    await page.locator('.section-sources summary').first().click();
    const sourceID = await sourceButton.getAttribute('data-source');
    await sourceButton.click();
    await page.locator('.source-actions').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.transcript-return').waitFor();
    assert.equal(await page.locator('.transcript-return').count(), 1,
      'Transcript origin remains available after a source visit and return');
    assert.equal(new URL(page.url()).hash, '#edition/weekly/weekly-sample-001',
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
    assert.notEqual(new URL(page.url()).hash, '#edition/weekly/weekly-sample-001',
      'Contextual return does not leave a duplicate transcript entry for browser Back');

    await page.evaluate(() => { location.hash = '#explanation/dive-payments-basics'; });
    await page.locator('.deep-dive-actions').waitFor();
    const diveAudio = page.locator('.deep-dive-actions [data-audio-action]');
    await diveAudio.click();
    await page.waitForFunction(() => document.querySelector('.mini-player-toggle')?.getAttribute('aria-label')?.startsWith('Pause'));
    await page.locator('.mini-player-open').click();
    await page.locator('[data-player-toggle]').click();
    await page.waitForFunction(() => /^Resume/.test(document.querySelector('[data-player-toggle]')?.textContent || ''));
    await page.locator('[data-player-close]').click();
    await page.evaluate(() => { location.hash = '#library'; });
    await page.locator('.library-filters').waitFor();
    const beforeDive = await page.evaluate(() => ({
      active: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      playerLabel: document.querySelector('.mini-player-toggle')?.getAttribute('aria-label'),
      time: document.querySelector('.mini-player-open small')?.textContent,
    }));
    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read transcript' }).click();
    await page.locator('.deep-dive-sources').waitFor();
    assert.equal(new URL(page.url()).hash, '#explanation/dive-payments-basics', 'Explanation recording resolves by stable item ID');
    assert.deepEqual(await page.evaluate(() => ({
      active: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      playerLabel: document.querySelector('.mini-player-toggle')?.getAttribute('aria-label'),
      time: document.querySelector('.mini-player-open small')?.textContent,
    })), beforeDive, 'Explanation transcript navigation preserves playback and position');
    assert.equal(await page.locator('.reader-back').textContent(), '‹ Library',
      'Explanation return preserves the Library origin');
    await page.locator('.transcript-return').click();
    await page.waitForFunction(() => location.hash === '#library');
    await page.waitForFunction(() => document.activeElement?.classList.contains('mini-player-open'));
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('mini-player-open')), true,
      'Library return restores focus to the mini-player');
    assert.deepEqual(errors, []);

    await page.addInitScript(() => {
      const state = JSON.parse(localStorage.getItem('briefing:v1:state'));
      state.activeAudio = { id: 'weekly-sample-001', revisionId: 'sample-week-r3' };
      localStorage.setItem('briefing:v1:state', JSON.stringify(state));
    });
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.mini-player-open').waitFor();
    assert.equal(await page.locator('.mini-player-open strong').textContent(), 'Superseded fictional weekly version',
      'A restored old recording remains associated with its exact revision until transcript handoff');
    await page.locator('.mini-player-toggle').click();
    await page.waitForFunction(() => document.querySelector('.mini-player-toggle')?.getAttribute('aria-label')?.startsWith('Pause'));
    await page.evaluate(() => { location.hash = '#library'; });
    await page.locator('.library-filters').waitFor();
    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read corrected edition' }).click();
    await page.locator('[data-edition="weekly-sample-001"] .correction-notice').waitFor();
    assert.equal(await page.locator('.edition h1').textContent(), 'Five systems, one useful question',
      'The superseded weekly recording resolves to the current correction');
    assert.match(await page.locator('.mini-player-toggle').getAttribute('aria-label'), /^Resume/,
      'Old playback is stopped before current corrected text appears');
    assert.equal(await page.locator('.mini-player-open small').textContent(), '0:00 of 0:18',
      'Old recording seconds are not applied to the current revision');

    const badAudio = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await badAudio.route('**/content/deep-dives.json', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const dive = data.deep_dives[0];
      dive.audio = { state: 'recorded', revision_id: dive.revision_id, script_hash: 'mismatched-fictional-hash', url: '/content/audio/test.wav' };
      await route.fulfill({ response, body: JSON.stringify(data) });
    });
    await badAudio.goto(baseURL, { waitUntil: 'networkidle' });
    await badAudio.evaluate(() => { location.hash = '#explanation/dive-payments-basics'; });
    await badAudio.locator('.deep-dive-actions').waitFor();
    assert.equal(await badAudio.locator('.deep-dive-actions [data-audio-action]').count(), 0,
      'An explanation with mismatched recorded audio has no playable action');
    assert.equal(await badAudio.locator('.deep-dive-actions').textContent(), 'Audio unavailable for this version.');
    await badAudio.close();

    const staleExplanation = await browser.newPage({ viewport: { width: 390, height: 844 } });
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
    await staleExplanation.goto(baseURL, { waitUntil: 'networkidle' });
    await staleExplanation.locator('.mini-player-open').click();
    await staleExplanation.getByRole('button', { name: 'Read current explanation' }).waitFor();
    await staleExplanation.getByRole('button', { name: 'Read current explanation' }).click();
    await staleExplanation.locator('.deep-dive-sources').waitFor();
    assert.equal(await staleExplanation.locator('h1').textContent(), 'Why does a payment that looks instant still need settlement?',
      'A stale explanation recording opens the current item text');
    await staleExplanation.waitForFunction(() => document.activeElement?.tagName === 'H1');
    assert.equal(new URL(staleExplanation.url()).hash, '#explanation/dive-payments-basics');
    assert.equal(await staleExplanation.locator('.deep-dive-actions [data-audio-action]').count(), 0,
      'Pending current explanation audio remains unavailable after stale recording handoff');
    await staleExplanation.close();

    const beforeMissingTranscript = new URL(page.url()).hash;
    await page.evaluate(async () => {
      const { createPlayer } = await import('/src/player.js');
      const root = document.createElement('div'); root.id = 'missing-item-player-test'; document.body.append(root);
      window.missingItemPlayerTest = createPlayer(root, () => {}, () => null, () => {}, () => ({ available: false }));
      window.missingItemPlayerTest.play({ id: 'fictional-item-absent-from-pack', title: 'Unavailable fictional recording',
        kind: 'weekly', revision_id: 'fictional-r1', script_hash: 'fictional-hash', audio: { state: 'pending' }, sections: [] });
    });
    const missingDialog = page.locator('#missing-item-player-test .player-dialog');
    await missingDialog.waitFor();
    await missingDialog.locator('[data-player-transcript]').click();
    assert.equal(await missingDialog.isVisible(), true, 'Unavailable transcript keeps the expanded player open');
    assert.equal(await missingDialog.locator('[data-player-transcript-error]').textContent(), 'This transcript is unavailable.',
      'Unavailable transcript gives a useful in-player message');
    assert.equal(new URL(page.url()).hash, beforeMissingTranscript, 'Unavailable transcript does not navigate');

    const ambiguous = await browser.newPage({ viewport: { width: 390, height: 844 } });
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
    await ambiguous.goto(baseURL, { waitUntil: 'networkidle' });
    await ambiguous.getByRole('heading', { name: 'This page is unavailable' }).waitFor();
    await ambiguous.locator('.mini-player-open').click();
    await ambiguous.getByRole('button', { name: 'Read transcript' }).click();
    const ambiguousDialog = ambiguous.locator('.player-dialog');
    assert.equal(await ambiguousDialog.isVisible(), true, 'Ambiguous current revisions keep the player open');
    assert.equal(await ambiguousDialog.locator('[data-player-transcript-error]').textContent(), 'This transcript is unavailable.',
      'Ambiguous revisions fail closed instead of showing stale text');
    await ambiguous.close();

    const withdrawn = await browser.newPage({ viewport: { width: 390, height: 844 } });
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
    await withdrawn.goto(baseURL, { waitUntil: 'networkidle' });
    await withdrawn.getByRole('heading', { name: 'This page is unavailable' }).waitFor();
    await withdrawn.locator('.mini-player-open').click();
    await withdrawn.getByRole('button', { name: 'Read transcript' }).click();
    const withdrawnDialog = withdrawn.locator('.player-dialog');
    assert.equal(await withdrawnDialog.isVisible(), true, 'A fully superseded item keeps the player open');
    assert.equal(await withdrawnDialog.locator('[data-player-transcript-error]').textContent(), 'This transcript is unavailable.',
      'No stale revision is used when every revision is superseded');
    await withdrawn.close();
    console.log('Exact-item transcript routing, playback continuity, contextual return and focus passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
